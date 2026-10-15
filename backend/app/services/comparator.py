import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Resume, UserProfile
from app.schemas.analysis import (
    CategoryEvaluation,
    JobRequirementsExtraction,
    ProfileAndResumeComparison,
    ProfileFitLabel,
    ResumeFitRecommendation,
)

CLEARANCE_RANK = {
    "none": 0,
    "public_trust": 1,
    "secret": 2,
    "top_secret": 3,
    "ts_sci": 4,
    "ts_sci_poly": 5,
}

DEGREE_RANK = {
    "none": 0,
    "high_school": 1,
    "associate": 2,
    "bachelor": 3,
    "master": 4,
    "phd": 5,
}


def _normalize_date_str(val: str | None) -> str | None:
    if not val:
        return None
    m = re.search(r"(20\d{2})(?:-(\d{2}))?", val.strip())
    if not m:
        return None
    yr = m.group(1)
    mo = m.group(2) or "05"
    return f"{yr}-{mo}"


def evaluate_extraction_against_profile_and_resumes(
    db: Session,
    extraction: JobRequirementsExtraction,
    target_resume_id: str | None = None,
) -> ProfileAndResumeComparison:
    """
    Deterministically compares extracted job restrictions against the local UserProfile
    and stored Resume records. Never treats missing profile fields as disqualifications.
    """
    profile = db.get(UserProfile, 1)
    active_resumes = list(
        db.scalars(
            select(Resume).where(Resume.is_active.is_(True)).order_by(Resume.is_default.desc())
        ).all()
    )

    evaluations: dict[str, CategoryEvaluation] = {}

    # 1. Work Authorization / Citizenship
    wa = extraction.work_authorization
    if wa.status == "not_stated":
        evaluations["work_authorization"] = CategoryEvaluation(
            category="work_authorization",
            fit_label="not_applicable",
            reason="Not stated in posting",
        )
    elif not wa.quote_verified:
        evaluations["work_authorization"] = CategoryEvaluation(
            category="work_authorization",
            fit_label="needs_review",
            reason="Quote could not be verified verbatim; review manually",
        )
    elif not profile or (profile.is_us_citizen is None and profile.authorized_to_work_us is None):
        evaluations["work_authorization"] = CategoryEvaluation(
            category="work_authorization",
            fit_label="profile_missing",
            reason="Set citizenship/work authorization in your local profile to compare",
        )
    elif wa.us_citizenship_required:
        if profile.is_us_citizen is True:
            evaluations["work_authorization"] = CategoryEvaluation(
                category="work_authorization",
                fit_label="likely_meets",
                reason="Profile indicates U.S. citizenship",
            )
        elif profile.is_us_citizen is False:
            evaluations["work_authorization"] = CategoryEvaluation(
                category="work_authorization",
                fit_label="possible_mismatch",
                reason="Job requires U.S. citizenship",
            )
        else:
            evaluations["work_authorization"] = CategoryEvaluation(
                category="work_authorization",
                fit_label="profile_missing",
                reason="U.S. citizenship status not set in profile",
            )
    else:
        if profile.authorized_to_work_us is True or profile.is_us_citizen is True:
            evaluations["work_authorization"] = CategoryEvaluation(
                category="work_authorization",
                fit_label="likely_meets",
                reason="Profile indicates U.S. work authorization",
            )
        elif profile.authorized_to_work_us is False:
            evaluations["work_authorization"] = CategoryEvaluation(
                category="work_authorization",
                fit_label="possible_mismatch",
                reason="Job requires work authorization",
            )
        else:
            evaluations["work_authorization"] = CategoryEvaluation(
                category="work_authorization",
                fit_label="needs_review",
                reason="Review stated work authorization terms",
            )

    # 2. Visa Sponsorship
    sp = extraction.sponsorship
    if sp.status == "not_stated":
        evaluations["sponsorship"] = CategoryEvaluation(
            category="sponsorship",
            fit_label="not_applicable",
            reason="Not stated in posting",
        )
    elif not sp.quote_verified:
        evaluations["sponsorship"] = CategoryEvaluation(
            category="sponsorship",
            fit_label="needs_review",
            reason="Unverified quote; review manually",
        )
    elif not profile or profile.requires_visa_sponsorship is None:
        evaluations["sponsorship"] = CategoryEvaluation(
            category="sponsorship",
            fit_label="profile_missing",
            reason="Set visa sponsorship preference in local profile to compare",
        )
    elif profile.requires_visa_sponsorship is False:
        evaluations["sponsorship"] = CategoryEvaluation(
            category="sponsorship",
            fit_label="likely_meets",
            reason="You do not require visa sponsorship",
        )
    else:
        if sp.sponsorship_offered == "no":
            evaluations["sponsorship"] = CategoryEvaluation(
                category="sponsorship",
                fit_label="possible_mismatch",
                reason="Posting states visa sponsorship is not offered",
            )
        elif sp.sponsorship_offered == "yes":
            evaluations["sponsorship"] = CategoryEvaluation(
                category="sponsorship",
                fit_label="likely_meets",
                reason="Posting indicates sponsorship is available",
            )
        else:
            evaluations["sponsorship"] = CategoryEvaluation(
                category="sponsorship",
                fit_label="needs_review",
                reason="Sponsorship is case-by-case or ambiguous",
            )

    # 3. Security Clearance
    cl = extraction.security_clearance
    if cl.status == "not_stated" or cl.level == "none":
        evaluations["security_clearance"] = CategoryEvaluation(
            category="security_clearance",
            fit_label="not_applicable",
            reason="No security clearance required",
        )
    elif not cl.quote_verified:
        evaluations["security_clearance"] = CategoryEvaluation(
            category="security_clearance",
            fit_label="needs_review",
            reason="Unverified clearance quote; review manually",
        )
    elif not profile or not profile.clearance_level:
        evaluations["security_clearance"] = CategoryEvaluation(
            category="security_clearance",
            fit_label="profile_missing",
            reason="Set security clearance level in local profile to compare",
        )
    else:
        user_rank = CLEARANCE_RANK.get(profile.clearance_level, 0)
        req_rank = CLEARANCE_RANK.get(cl.level, 99)
        if user_rank >= req_rank:
            evaluations["security_clearance"] = CategoryEvaluation(
                category="security_clearance",
                fit_label="likely_meets",
                reason=f"Your clearance ({profile.clearance_level}) meets or exceeds {cl.level}",
            )
        elif cl.timing == "eligible_to_obtain":
            if profile.is_us_citizen is False:
                evaluations["security_clearance"] = CategoryEvaluation(
                    category="security_clearance",
                    fit_label="possible_mismatch",
                    reason="Clearance eligibility typically requires U.S. citizenship",
                )
            else:
                evaluations["security_clearance"] = CategoryEvaluation(
                    category="security_clearance",
                    fit_label="needs_review",
                    reason=f"Requires eligibility to obtain {cl.level} clearance",
                )
        else:
            evaluations["security_clearance"] = CategoryEvaluation(
                category="security_clearance",
                fit_label="possible_mismatch",
                reason=f"Requires active {cl.level} clearance (profile: {profile.clearance_level})",
            )

    # 4. Education
    ed = extraction.education
    if ed.status == "not_stated" or ed.minimum_degree in ("none", "unspecified"):
        evaluations["education"] = CategoryEvaluation(
            category="education",
            fit_label="not_applicable",
            reason="No minimum degree stated",
        )
    elif not ed.quote_verified:
        evaluations["education"] = CategoryEvaluation(
            category="education",
            fit_label="needs_review",
            reason="Unverified education quote; review manually",
        )
    elif not profile or not profile.highest_degree:
        evaluations["education"] = CategoryEvaluation(
            category="education",
            fit_label="profile_missing",
            reason="Set highest degree in local profile (or sync from resume)",
        )
    else:
        user_deg = DEGREE_RANK.get(profile.highest_degree, 0)
        req_deg = DEGREE_RANK.get(ed.minimum_degree, 0)
        if user_deg >= req_deg:
            evaluations["education"] = CategoryEvaluation(
                category="education",
                fit_label="likely_meets",
                reason=f"Profile degree ({profile.highest_degree}) meets minimum ({ed.minimum_degree})",
            )
        elif ed.accepts_equivalent_experience:
            evaluations["education"] = CategoryEvaluation(
                category="education",
                fit_label="needs_review",
                reason=f"Prefers {ed.minimum_degree}, but accepts equivalent experience",
            )
        else:
            evaluations["education"] = CategoryEvaluation(
                category="education",
                fit_label="possible_mismatch",
                reason=f"Requires {ed.minimum_degree} degree (profile: {profile.highest_degree})",
            )

    # 5. Graduation Window
    gw = extraction.graduation_window
    if gw.status == "not_stated":
        evaluations["graduation_window"] = CategoryEvaluation(
            category="graduation_window",
            fit_label="not_applicable",
            reason="No graduation window stated",
        )
    elif not gw.quote_verified:
        evaluations["graduation_window"] = CategoryEvaluation(
            category="graduation_window",
            fit_label="needs_review",
            reason="Unverified graduation window quote",
        )
    elif not profile or not profile.graduation_date:
        evaluations["graduation_window"] = CategoryEvaluation(
            category="graduation_window",
            fit_label="profile_missing",
            reason="Set graduation date (YYYY-MM) in profile to compare",
        )
    else:
        user_grad = _normalize_date_str(profile.graduation_date)
        early = _normalize_date_str(gw.earliest_graduation)
        late = _normalize_date_str(gw.latest_graduation)
        if not user_grad or (not early and not late):
            evaluations["graduation_window"] = CategoryEvaluation(
                category="graduation_window",
                fit_label="needs_review",
                reason="Verify your graduation date against the stated window",
            )
        elif (early and user_grad < early) or (late and user_grad > late):
            evaluations["graduation_window"] = CategoryEvaluation(
                category="graduation_window",
                fit_label="possible_mismatch",
                reason=f"Profile graduation ({user_grad}) is outside stated window ({early or '*'} to {late or '*'})",
            )
        else:
            evaluations["graduation_window"] = CategoryEvaluation(
                category="graduation_window",
                fit_label="likely_meets",
                reason=f"Profile graduation ({user_grad}) fits stated window",
            )

    # 6. Experience
    ex = extraction.experience
    if ex.status == "not_stated" or ex.min_years_required is None:
        evaluations["experience"] = CategoryEvaluation(
            category="experience",
            fit_label="not_applicable",
            reason="No explicit minimum years of experience stated",
        )
    elif not ex.quote_verified:
        evaluations["experience"] = CategoryEvaluation(
            category="experience",
            fit_label="needs_review",
            reason="Unverified experience quote; review manually",
        )
    elif not profile or profile.years_experience_total is None:
        evaluations["experience"] = CategoryEvaluation(
            category="experience",
            fit_label="profile_missing",
            reason="Set total years of experience in local profile to compare",
        )
    else:
        diff = profile.years_experience_total - ex.min_years_required
        if diff >= 0:
            evaluations["experience"] = CategoryEvaluation(
                category="experience",
                fit_label="likely_meets",
                reason=f"Your {profile.years_experience_total} yrs meets minimum {ex.min_years_required} yrs",
            )
        elif diff >= -1.0 or ex.status == "preferred":
            evaluations["experience"] = CategoryEvaluation(
                category="experience",
                fit_label="needs_review",
                reason=f"Stated {ex.min_years_required} yrs vs your {profile.years_experience_total} yrs (close match)",
            )
        else:
            evaluations["experience"] = CategoryEvaluation(
                category="experience",
                fit_label="possible_mismatch",
                reason=f"Requires {ex.min_years_required}+ yrs (profile: {profile.years_experience_total} yrs)",
            )

    # 7. Location & Work Mode
    loc = extraction.location_work_mode
    if loc.status == "not_stated":
        evaluations["location_work_mode"] = CategoryEvaluation(
            category="location_work_mode",
            fit_label="not_applicable",
            reason="Not stated in posting",
        )
    elif not loc.quote_verified:
        evaluations["location_work_mode"] = CategoryEvaluation(
            category="location_work_mode",
            fit_label="needs_review",
            reason="Unverified location quote; review manually",
        )
    elif not profile or not profile.preferred_work_modes:
        evaluations["location_work_mode"] = CategoryEvaluation(
            category="location_work_mode",
            fit_label="profile_missing",
            reason="Set preferred work modes in local profile to compare",
        )
    else:
        try:
            pref_modes = json.loads(profile.preferred_work_modes)
        except ValueError:
            pref_modes = []
        if not pref_modes:
            evaluations["location_work_mode"] = CategoryEvaluation(
                category="location_work_mode",
                fit_label="profile_missing",
                reason="Set preferred work modes in local profile",
            )
        elif loc.work_mode != "not_stated" and loc.work_mode not in pref_modes:
            evaluations["location_work_mode"] = CategoryEvaluation(
                category="location_work_mode",
                fit_label="possible_mismatch",
                reason=f"Job is {loc.work_mode}, which is not in your preferred modes ({', '.join(pref_modes)})",
            )
        else:
            evaluations["location_work_mode"] = CategoryEvaluation(
                category="location_work_mode",
                fit_label="likely_meets",
                reason=f"Work mode ({loc.work_mode}) aligns with your preferences",
            )

    # --- RESUME FIT & MISSING SKILL RECOMMENDATION ---
    required_keywords: list[str] = []
    for d in ex.domains:
        if d.strip() and d.strip() not in required_keywords:
            required_keywords.append(d.strip())
    for item in extraction.other_restrictions:
        if item.category_label.lower() in ("skill", "certification", "technology", "tool"):
            required_keywords.append(item.summary)

    resume_rec = ResumeFitRecommendation()
    if active_resumes:
        # Score either the requested resume_id or find the best matching active resume
        candidates_to_score = active_resumes
        if target_resume_id:
            specific = [r for r in active_resumes if r.id == target_resume_id]
            if specific:
                candidates_to_score = specific

        best_resume: Resume = candidates_to_score[0]
        best_matched: list[str] = []
        best_missing: list[str] = []
        best_ratio: float = -1.0

        for res in candidates_to_score:
            res_text_lower = (res.content_text or "").lower()
            matched: list[str] = []
            missing: list[str] = []
            for kw in required_keywords:
                if kw.lower() in res_text_lower:
                    matched.append(kw)
                else:
                    missing.append(kw)
            ratio = (len(matched) / len(required_keywords)) if required_keywords else 1.0
            if ratio > best_ratio:
                best_ratio = ratio
                best_resume = res
                best_matched = matched
                best_missing = missing

        resume_rec = ResumeFitRecommendation(
            recommended_resume_id=best_resume.id,
            recommended_resume_label=best_resume.label,
            matched_skills=best_matched,
            missing_skills=best_missing,
            coverage_ratio=round(best_ratio, 2) if required_keywords else 1.0,
            note=(
                f"Best fit: {best_resume.label} ({len(best_matched)}/{len(required_keywords)} domain keywords found)"
                if required_keywords
                else f"Default resume selected: {best_resume.label}"
            ),
        )

    # Compute overall fit roll-up
    labels = [ev.fit_label for ev in evaluations.values()]
    if "possible_mismatch" in labels:
        overall: ProfileFitLabel = "possible_mismatch"
    elif "needs_review" in labels or extraction.contradictions_or_ambiguities:
        overall = "needs_review"
    elif "likely_meets" in labels:
        overall = "likely_meets"
    elif "profile_missing" in labels:
        overall = "profile_missing"
    else:
        overall = "not_applicable"

    return ProfileAndResumeComparison(
        overall_fit=overall,
        evaluations=evaluations,
        resume_recommendation=resume_rec,
    )
