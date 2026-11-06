import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.entities import Job, JobApplication, utcnow
from app.schemas.jobs import MatchState, MatchTier
from app.services.normalizer import (
    NormalizedPostingMetadata,
    clean_description_and_hash,
    company_similarity,
    compute_title_similarity,
    extract_core_job_title,
    is_generic_ats_flow_url,
    is_generic_company_name,
    is_placeholder_or_confirmation_title,
)

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "with",
    "we",
    "you",
    "our",
    "your",
    "will",
    "role",
    "job",
    "team",
    "work",
    "working",
    "about",
    "this",
    "have",
    "has",
    "can",
    "all",
    "more",
    "who",
    "what",
    "their",
    "they",
}


@dataclass
class ScoredCandidate:
    job: Job
    tier: MatchTier
    confidence: float
    reasons: list[str] = field(default_factory=list)
    conflicting_req_ids: bool = False


@dataclass
class ResolutionOutcome:
    match_state: MatchState
    match_tier: MatchTier
    matched_job: Job | None = None
    candidates: list[ScoredCandidate] = field(default_factory=list)


def _tokenize_ordered(text: str | None) -> list[str]:
    if not text:
        return []
    words = re.findall(r"[a-z0-9]{2,}", text.lower())
    return [w for w in words if w not in STOPWORDS]


def _tokenize(text: str | None) -> set[str]:
    return set(_tokenize_ordered(text))


def _bigrams(tokens: list[str]) -> set[tuple[str, str]]:
    if len(tokens) < 2:
        return set()
    return {(tokens[i], tokens[i + 1]) for i in range(len(tokens) - 1)}


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def compute_description_similarity(desc_a: str | None, desc_b: str | None) -> float:
    """
    Computes robust description similarity (0.0 to 1.0) across job boards and reposts.
    Combines:
    - Token Jaccard similarity
    - Token Dice similarity
    - Token Containment (handles when one board includes extra EEO/company boilerplate or a shorter excerpt)
    - Word Bigram (2-word technical phrase) overlap & containment
    """
    if not desc_a or not desc_b:
        return 0.0

    clean_a, hash_a = clean_description_and_hash(desc_a)
    clean_b, hash_b = clean_description_and_hash(desc_b)
    if not clean_a or not clean_b:
        return 0.0
    if hash_a and hash_a == hash_b:
        return 1.0

    ordered_a = _tokenize_ordered(clean_a)
    ordered_b = _tokenize_ordered(clean_b)
    set_a = set(ordered_a)
    set_b = set(ordered_b)
    if not set_a or not set_b:
        return 0.0

    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    min_len = min(len(set_a), len(set_b))

    jaccard = inter / union if union > 0 else 0.0
    dice = (2.0 * inter) / (len(set_a) + len(set_b))
    containment = (inter / min_len) if min_len >= 12 else jaccard

    bg_a = _bigrams(ordered_a)
    bg_b = _bigrams(ordered_b)
    bg_sim = 0.0
    if bg_a and bg_b:
        bg_inter = len(bg_a & bg_b)
        bg_min = min(len(bg_a), len(bg_b))
        bg_dice = (2.0 * bg_inter) / (len(bg_a) + len(bg_b))
        bg_contain = (bg_inter / bg_min) if bg_min >= 10 else bg_dice
        bg_sim = max(bg_dice, bg_contain * 0.92)

    best = max(
        jaccard,
        dice * 0.92,
        containment * 0.88 if min_len >= 15 else 0.0,
        bg_sim,
    )
    return round(min(best, 1.0), 2)


def _has_conflicting_req_ids(incoming_req: str | None, job: Job) -> bool:
    if not incoming_req:
        return False
    inc = incoming_req.strip().upper()
    if job.employer_req_id and job.employer_req_id.strip().upper() != inc:
        return True
    for p in job.postings:
        if p.requisition_id and p.requisition_id.strip().upper() != inc:
            return True
    return False


def _is_dismissed(norm: NormalizedPostingMetadata, job: Job) -> bool:
    for d in job.dismissed_duplicates:
        if d.dismissed_url_norm and d.dismissed_url_norm == norm.source_url_norm:
            return True
        if (
            d.dismissed_platform
            and d.dismissed_posting_id
            and d.dismissed_platform == norm.platform
            and d.dismissed_posting_id == norm.source_posting_id
        ):
            return True
    return False


def _job_status_to_match_state(status: str) -> MatchState:
    if status in ("applied", "interviewing", "offer", "rejected", "withdrawn"):
        return "previously_applied"
    return "saved_in_progress"


def _status_priority(status: str) -> int:
    if status in ("applied", "interviewing", "offer"):
        return 2
    if status in ("rejected", "withdrawn"):
        return 1
    return 0


def _self_heal_job_metadata(
    db: Session,
    job: Job,
    norm: NormalizedPostingMetadata,
    raw_company: str | None = None,
    raw_title: str | None = None,
    raw_description: str | None = None,
) -> None:
    """
    If a stored job was captured with a placeholder title ('Thank You') or a generic
    board company ('linkedin') or missing description, self-heal it when matched.
    """
    changed = False
    if (
        is_generic_company_name(job.company_norm)
        and not is_generic_company_name(norm.company_norm)
    ):
        job.company_norm = norm.company_norm
        job.company_raw = (raw_company or norm.company_norm).strip()
        changed = True

    if (
        is_placeholder_or_confirmation_title(job.title_raw)
        and not is_placeholder_or_confirmation_title(norm.title_norm)
    ):
        job.title_norm = norm.title_norm
        job.title_raw = (raw_title or norm.title_norm).strip()
        changed = True

    if not job.location_norm and norm.location_norm:
        job.location_norm = norm.location_norm
        if not job.location_raw:
            job.location_raw = norm.location_norm
        changed = True

    if not job.work_mode and norm.work_mode:
        job.work_mode = norm.work_mode
        changed = True

    if not job.employer_req_id and norm.requisition_id:
        job.employer_req_id = norm.requisition_id
        changed = True

    if norm.cleaned_text_hash and job.postings:
        first_posting = job.postings[0]
        if not first_posting.cleaned_text_hash:
            first_posting.cleaned_text_hash = norm.cleaned_text_hash
            if raw_description and not first_posting.description_raw:
                first_posting.description_raw = raw_description
            changed = True

    if changed:
        job.updated_at = utcnow()
        db.commit()


def resolve_posting_against_db(
    db: Session,
    norm: NormalizedPostingMetadata,
    raw_company: str | None = None,
    raw_title: str | None = None,
    raw_description: str | None = None,
) -> ResolutionOutcome:
    """
    Executes the multi-signal matching strategy against SQLite:
    - Tier 1: Exact match by (platform, source_posting_id), non-generic source_url_norm,
              (company, employer_req_id), or same-platform repost of the same opening.
    - Tier 2: Strong candidate across platforms/reposts by canonical_ats_url,
              identical cleaned_text_hash, or high company + core-title + description similarity.
    - Tier 3: Possible duplicate by company + fuzzy title/location/description similarity.
    - Tier 4: Unmatched.
    """
    stmt = (
        select(Job)
        .options(
            selectinload(Job.postings),
            selectinload(Job.events),
            selectinload(Job.resume),
            selectinload(Job.applications).selectinload(JobApplication.resume),
            selectinload(Job.dismissed_duplicates),
        )
        .order_by(Job.updated_at.desc())
        .execution_options(populate_existing=True)
    )
    all_jobs = list(db.scalars(stmt).all())

    # --- TIER 1: Exact Identity Check ---
    for job in all_jobs:
        if _has_conflicting_req_ids(norm.requisition_id, job):
            continue

        # 1a. Check linked postings for exact platform+ID or normalized non-generic URL
        for posting in job.postings:
            if (
                norm.source_posting_id
                and posting.platform == norm.platform
                and posting.source_posting_id == norm.source_posting_id
            ):
                posting.last_seen_at = utcnow()
                _self_heal_job_metadata(db, job, norm, raw_company, raw_title, raw_description)
                db.commit()
                return ResolutionOutcome(
                    match_state=_job_status_to_match_state(job.status),
                    match_tier="EXACT_MATCH",
                    matched_job=job,
                )
            if (
                not is_generic_ats_flow_url(norm.source_url_norm)
                and not is_generic_ats_flow_url(posting.source_url_norm)
                and posting.source_url_norm == norm.source_url_norm
            ):
                posting.last_seen_at = utcnow()
                _self_heal_job_metadata(db, job, norm, raw_company, raw_title, raw_description)
                db.commit()
                return ResolutionOutcome(
                    match_state=_job_status_to_match_state(job.status),
                    match_tier="EXACT_MATCH",
                    matched_job=job,
                )

        # 1b. Exact verified requisition ID within the same/compatible company
        comp_sim = company_similarity(norm.company_norm, job.company_norm)
        either_comp_generic = is_generic_company_name(norm.company_norm) or is_generic_company_name(
            job.company_norm
        )
        if (
            norm.requisition_id
            and (comp_sim >= 0.80 or either_comp_generic)
            and (
                (job.employer_req_id and job.employer_req_id.upper() == norm.requisition_id.upper())
                or any(
                    p.requisition_id and p.requisition_id.upper() == norm.requisition_id.upper()
                    for p in job.postings
                )
            )
        ):
            _self_heal_job_metadata(db, job, norm, raw_company, raw_title, raw_description)
            return ResolutionOutcome(
                match_state=_job_status_to_match_state(job.status),
                match_tier="EXACT_MATCH",
                matched_job=job,
            )

    # 1c. Same opening seen again (repost with new posting ID on the same platform, or returning after ATS flow)
    same_opening: list[Job] = []
    for job in all_jobs:
        if _is_dismissed(norm, job) or _has_conflicting_req_ids(norm.requisition_id, job):
            continue

        comp_sim = company_similarity(norm.company_norm, job.company_norm)
        either_comp_generic = is_generic_company_name(norm.company_norm) or is_generic_company_name(
            job.company_norm
        )
        title_sim = compute_title_similarity(
            raw_title or norm.title_norm,
            job.title_raw or job.title_norm,
        )
        either_title_placeholder = is_placeholder_or_confirmation_title(
            raw_title or norm.title_norm
        ) or is_placeholder_or_confirmation_title(job.title_raw or job.title_norm)

        same_desc_hash = bool(
            norm.cleaned_text_hash
            and any(p.cleaned_text_hash == norm.cleaned_text_hash for p in job.postings)
        )
        max_desc_sim = 0.0
        if norm.cleaned_text:
            for p in job.postings:
                if p.description_raw:
                    max_desc_sim = max(
                        max_desc_sim,
                        compute_description_similarity(norm.cleaned_text, p.description_raw),
                    )

        platforms = {
            p.platform
            for p in job.postings
            if p.platform and p.platform not in ("other", "manual")
        }
        incoming_known_platform = (
            norm.platform if norm.platform and norm.platform not in ("other", "manual") else None
        )
        # Keep cross-platform matches (e.g. greenhouse vs linkedin) for Tier 2 so they surface as STRONG_CANDIDATE
        if incoming_known_platform and platforms and incoming_known_platform not in platforms:
            continue

        # Match if same company + same core title, OR same company + identical description,
        # OR same company where one record was an ATS "Thank You" confirmation page,
        # OR generic company ("linkedin") with identical description or matching core title + description.
        is_same_role = False
        if comp_sim >= 0.85:
            if title_sim >= 0.90 or same_desc_hash:
                is_same_role = True
            elif title_sim >= 0.70 and max_desc_sim >= 0.55:
                is_same_role = True
            elif max_desc_sim >= 0.78:
                is_same_role = True
            elif either_title_placeholder:
                is_same_role = True
        elif either_comp_generic:
            if same_desc_hash:
                is_same_role = True
            elif title_sim >= 0.90 and (max_desc_sim >= 0.45 or not norm.cleaned_text):
                is_same_role = True

        if is_same_role:
            same_opening.append(job)

    if same_opening:
        same_opening.sort(
            key=lambda j: (_status_priority(j.status), j.updated_at),
            reverse=True,
        )
        best_job = same_opening[0]
        _self_heal_job_metadata(db, best_job, norm, raw_company, raw_title, raw_description)
        return ResolutionOutcome(
            match_state=_job_status_to_match_state(best_job.status),
            match_tier="EXACT_MATCH",
            matched_job=best_job,
        )

    # --- TIER 2 & TIER 3: Cross-Platform & Fuzzy Repost Candidate Scoring ---
    candidates: list[ScoredCandidate] = []

    for job in all_jobs:
        if _is_dismissed(norm, job):
            continue

        # CRITICAL GUARDRAIL: Never suggest merging two postings that have DIFFERENT requisition IDs
        if _has_conflicting_req_ids(norm.requisition_id, job):
            continue

        reasons: list[str] = []
        score = 0.0
        tier: MatchTier = "POSSIBLE_DUPLICATE"

        comp_sim = company_similarity(norm.company_norm, job.company_norm)
        either_comp_generic = is_generic_company_name(norm.company_norm) or is_generic_company_name(
            job.company_norm
        )
        title_sim = compute_title_similarity(
            raw_title or norm.title_norm,
            job.title_raw or job.title_norm,
        )
        either_title_placeholder = is_placeholder_or_confirmation_title(
            raw_title or norm.title_norm
        ) or is_placeholder_or_confirmation_title(job.title_raw or job.title_norm)

        same_desc_hash = bool(
            norm.cleaned_text_hash
            and any(p.cleaned_text_hash == norm.cleaned_text_hash for p in job.postings)
        )

        max_desc_sim = 0.0
        if norm.cleaned_text:
            for p in job.postings:
                if p.description_raw:
                    max_desc_sim = max(
                        max_desc_sim,
                        compute_description_similarity(norm.cleaned_text, p.description_raw),
                    )
        has_stored_desc = any(bool(p.description_raw and p.description_raw.strip()) for p in job.postings)

        # 1. Check canonical ATS URL match
        if (
            norm.canonical_ats_url
            and job.canonical_ats_url
            and not is_generic_ats_flow_url(norm.canonical_ats_url)
            and norm.canonical_ats_url == job.canonical_ats_url
        ):
            score = 0.96
            tier = "STRONG_CANDIDATE"
            reasons.append("Identical canonical ATS destination URL")

        # 2. Same or closely matching company name
        elif comp_sim >= 0.85:
            if title_sim >= 0.92 and same_desc_hash:
                score = 0.96
                tier = "STRONG_CANDIDATE"
                reasons.append("Same company, matching role title, and identical description text")
            elif same_desc_hash and (title_sim >= 0.45 or either_title_placeholder):
                score = 0.93
                tier = "STRONG_CANDIDATE"
                reasons.append("Same company and identical job description text")
            elif title_sim >= 0.90:
                score = 0.82
                reasons.append("Same company and matching role title")
                if norm.location_norm and job.location_norm == norm.location_norm:
                    score += 0.06
                    reasons.append("Matching location")
                if max_desc_sim >= 0.40:
                    score += min(0.11, max_desc_sim * 0.14)
                    reasons.append(f"{int(max_desc_sim * 100)}% description similarity")
                elif not norm.cleaned_text or not has_stored_desc:
                    score += 0.08
                if score >= 0.88:
                    tier = "STRONG_CANDIDATE"
            elif title_sim >= 0.60 and max_desc_sim >= 0.45:
                score = round(0.62 + (title_sim * 0.18) + (max_desc_sim * 0.18), 2)
                if score >= 0.88:
                    tier = "STRONG_CANDIDATE"
                reasons.append(
                    f"Same company, similar title ({int(title_sim * 100)}%), and {int(max_desc_sim * 100)}% description match"
                )
            elif max_desc_sim >= 0.62:
                score = round(0.66 + (max_desc_sim * 0.28), 2)
                if score >= 0.88:
                    tier = "STRONG_CANDIDATE"
                reasons.append(
                    f"Same company and high description similarity ({int(max_desc_sim * 100)}%)"
                )
            elif either_title_placeholder:
                score = 0.88
                tier = "STRONG_CANDIDATE"
                reasons.append("Same company (matches application confirmation record)")

        # 3. Generic/board company on one side OR cross-agency repost with identical/near-identical description
        elif same_desc_hash and (title_sim >= 0.50 or either_comp_generic or either_title_placeholder):
            score = 0.92
            tier = "STRONG_CANDIDATE"
            reasons.append("Identical job description text across postings")
        elif either_comp_generic and title_sim >= 0.88 and (max_desc_sim >= 0.40 or not norm.cleaned_text or not has_stored_desc):
            score = 0.88
            tier = "STRONG_CANDIDATE"
            reasons.append("Matching role title and description")
        elif title_sim >= 0.80 and max_desc_sim >= 0.72:
            score = round(0.65 + (max_desc_sim * 0.28), 2)
            if score >= 0.88:
                tier = "STRONG_CANDIDATE"
            reasons.append(
                f"Matching role title ({int(title_sim * 100)}%) and {int(max_desc_sim * 100)}% description similarity"
            )

        if score >= 0.55:
            candidates.append(
                ScoredCandidate(
                    job=job,
                    tier=tier,
                    confidence=round(min(score, 0.99), 2),
                    reasons=reasons,
                    conflicting_req_ids=False,
                )
            )

    candidates.sort(
        key=lambda c: (_status_priority(c.job.status), c.confidence, c.job.updated_at),
        reverse=True,
    )

    if candidates:
        top = candidates[0]
        if top.confidence >= 0.85:
            _self_heal_job_metadata(db, top.job, norm, raw_company, raw_title, raw_description)
        return ResolutionOutcome(
            match_state="possible_duplicate",
            match_tier=top.tier,
            matched_job=top.job if top.confidence >= 0.80 else None,
            candidates=candidates[:5],
        )

    return ResolutionOutcome(
        match_state="no_local_record",
        match_tier="NO_MATCH",
        matched_job=None,
        candidates=[],
    )
