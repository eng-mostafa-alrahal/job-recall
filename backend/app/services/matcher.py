import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.entities import Job, utcnow
from app.schemas.jobs import MatchState, MatchTier
from app.services.normalizer import NormalizedPostingMetadata, clean_description_and_hash

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


def _tokenize(text: str | None) -> set[str]:
    if not text:
        return set()
    words = re.findall(r"[a-z0-9]{2,}", text.lower())
    return {w for w in words if w not in STOPWORDS}


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


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


def resolve_posting_against_db(
    db: Session, norm: NormalizedPostingMetadata
) -> ResolutionOutcome:
    """
    Executes the 4-tier matching strategy against SQLite:
    - Tier 1: Exact match by (platform, source_posting_id), source_url_norm, or (company_norm, employer_req_id).
    - Tier 2: Strong candidate by canonical_ats_url or (company_norm + title_norm + identical cleaned_text_hash).
    - Tier 3: Possible duplicate by company_norm + fuzzy title/location/description similarity.
    - Tier 4: Unmatched.
    """
    # Load jobs with postings, events, resume, and dismissed_duplicates
    stmt = (
        select(Job)
        .options(
            selectinload(Job.postings),
            selectinload(Job.events),
            selectinload(Job.resume),
            selectinload(Job.dismissed_duplicates),
        )
        .order_by(Job.updated_at.desc())
        .execution_options(populate_existing=True)
    )
    all_jobs = list(db.scalars(stmt).all())

    # --- TIER 1: Exact Identity Check ---
    for job in all_jobs:
        # 1a. Check linked postings for exact platform+ID or normalized URL
        for posting in job.postings:
            if (
                norm.source_posting_id
                and posting.platform == norm.platform
                and posting.source_posting_id == norm.source_posting_id
            ):
                posting.last_seen_at = utcnow()
                db.commit()
                return ResolutionOutcome(
                    match_state=_job_status_to_match_state(job.status),
                    match_tier="EXACT_MATCH",
                    matched_job=job,
                )
            if (
                norm.source_url_norm != "manual://local"
                and posting.source_url_norm == norm.source_url_norm
            ):
                posting.last_seen_at = utcnow()
                db.commit()
                return ResolutionOutcome(
                    match_state=_job_status_to_match_state(job.status),
                    match_tier="EXACT_MATCH",
                    matched_job=job,
                )

        # 1b. Exact verified requisition ID within the same normalized company
        if (
            norm.requisition_id
            and job.company_norm == norm.company_norm
            and (
                (job.employer_req_id and job.employer_req_id.upper() == norm.requisition_id.upper())
                or any(
                    p.requisition_id and p.requisition_id.upper() == norm.requisition_id.upper()
                    for p in job.postings
                )
            )
        ):
            return ResolutionOutcome(
                match_state=_job_status_to_match_state(job.status),
                match_tier="EXACT_MATCH",
                matched_job=job,
            )

    # --- TIER 2 & TIER 3: Candidate Scoring ---
    candidates: list[ScoredCandidate] = []
    inc_title_tokens = _tokenize(norm.title_norm)
    inc_desc_tokens = _tokenize(norm.cleaned_text)

    for job in all_jobs:
        if _is_dismissed(norm, job):
            continue

        # CRITICAL GUARDRAIL: Never suggest merging two postings that have DIFFERENT requisition IDs
        if _has_conflicting_req_ids(norm.requisition_id, job):
            continue

        reasons: list[str] = []
        score = 0.0
        tier: MatchTier = "POSSIBLE_DUPLICATE"

        # Check canonical ATS URL match
        if (
            norm.canonical_ats_url
            and job.canonical_ats_url
            and norm.canonical_ats_url == job.canonical_ats_url
        ):
            score = 0.95
            tier = "STRONG_CANDIDATE"
            reasons.append("Identical canonical ATS destination URL")

        elif job.company_norm == norm.company_norm:
            # Same company — evaluate title, description hash, and fuzzy similarity
            exact_title = job.title_norm == norm.title_norm
            job_title_tokens = _tokenize(job.title_norm)
            title_sim = 1.0 if exact_title else _jaccard_similarity(inc_title_tokens, job_title_tokens)

            # Check if any posting on this job has the exact same cleaned_text_hash
            same_desc_hash = bool(
                norm.cleaned_text_hash
                and any(p.cleaned_text_hash == norm.cleaned_text_hash for p in job.postings)
            )

            # Compute max description token similarity across stored postings
            max_desc_sim = 0.0
            if inc_desc_tokens:
                for p in job.postings:
                    if p.description_raw:
                        cleaned_p, _ = clean_description_and_hash(p.description_raw)
                        p_tokens = _tokenize(cleaned_p)
                        max_desc_sim = max(
                            max_desc_sim, _jaccard_similarity(inc_desc_tokens, p_tokens)
                        )

            if exact_title and same_desc_hash:
                score = 0.94
                tier = "STRONG_CANDIDATE"
                reasons.append("Same company, normalized title, and identical description text")
            elif same_desc_hash and title_sim >= 0.5:
                score = 0.90
                tier = "STRONG_CANDIDATE"
                reasons.append("Same company and identical job description text")
            elif exact_title:
                score = 0.72
                reasons.append("Same company and normalized job title")
                if norm.location_norm and job.location_norm == norm.location_norm:
                    score += 0.10
                    reasons.append("Matching location")
                if max_desc_sim >= 0.45:
                    score += min(0.12, max_desc_sim * 0.15)
                    reasons.append(f"{int(max_desc_sim * 100)}% description token overlap")
                if score >= 0.90:
                    tier = "STRONG_CANDIDATE"
            elif title_sim >= 0.65 and max_desc_sim >= 0.50:
                score = round(0.55 + (title_sim * 0.2) + (max_desc_sim * 0.2), 2)
                reasons.append(
                    f"Same company, similar title ({int(title_sim * 100)}%), and {int(max_desc_sim * 100)}% description overlap"
                )
            elif max_desc_sim >= 0.70:
                score = round(0.60 + (max_desc_sim * 0.25), 2)
                reasons.append(
                    f"Same company and high description similarity ({int(max_desc_sim * 100)}%)"
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

    candidates.sort(key=lambda c: c.confidence, reverse=True)

    if candidates:
        top_tier = candidates[0].tier
        return ResolutionOutcome(
            match_state="possible_duplicate",
            match_tier=top_tier,
            matched_job=None,
            candidates=candidates[:5],
        )

    return ResolutionOutcome(
        match_state="no_local_record",
        match_tier="NO_MATCH",
        matched_job=None,
        candidates=[],
    )
