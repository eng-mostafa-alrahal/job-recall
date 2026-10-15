import asyncio
import re
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.entities import RequirementAnalysis, utcnow
from app.schemas.analysis import (
    AnalyzeJobResponse,
    ClearanceSlot,
    EducationSlot,
    EvidenceSlot,
    ExperienceSlot,
    GraduationWindowSlot,
    JobRequirementsExtraction,
    LocationSlot,
    SponsorshipSlot,
    WorkAuthSlot,
)
from app.services.comparator import evaluate_extraction_against_profile_and_resumes
from app.services.normalizer import clean_description_and_hash


class AnalyzerError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


SYSTEM_EXTRACTION_PROMPT = """You are a strict, evidence-based job eligibility extractor for JobRecall.
Analyze the provided job posting description and populate the structured output schema.

NON-NEGOTIABLE RULES:
1. Extract ONLY restrictions explicitly stated in the text. If a category is not mentioned, set its status to "not_stated" and leave summary and evidence_quote as null.
2. Every stated restriction ("required", "preferred", or "unclear") MUST include an exact, character-for-character verbatim substring from the job posting in `evidence_quote`. Never paraphrase or invent quotes.
3. Do NOT equate general work authorization ("must be authorized to work in the United States") with U.S. citizenship ("must be a U.S. citizen").
4. Record visa sponsorship separately from work authorization.
5. For security clearance, distinguish between an active clearance currently required ("active_required") vs eligibility/ability to obtain one ("eligible_to_obtain").
6. For education, distinguish minimum required degree from preferred degree, and note whether equivalent practical experience is accepted.
7. Flag any internal contradictions (e.g., header says Remote but body requires 3 days/week on-site) in `contradictions_or_ambiguities`.
"""


def verify_quote_in_text(quote: str | None, source_text: str) -> bool:
    """Deterministically verifies that `quote` appears verbatim in `source_text`."""
    if not quote or not quote.strip() or not source_text:
        return False
    norm_quote = re.sub(r"\s+", " ", quote.strip().lower())
    norm_source = re.sub(r"\s+", " ", source_text.strip().lower())
    if norm_quote in norm_source:
        return True
    alnum_quote = re.sub(r"[^a-z0-9\s]", "", norm_quote)
    alnum_quote = re.sub(r"\s+", " ", alnum_quote).strip()
    alnum_source = re.sub(r"[^a-z0-9\s]", "", norm_source)
    alnum_source = re.sub(r"\s+", " ", alnum_source).strip()
    return len(alnum_quote) >= 8 and alnum_quote in alnum_source


def validate_extraction_quotes(
    extraction: JobRequirementsExtraction, source_text: str
) -> JobRequirementsExtraction:
    """Runs deterministic substring verification on all slots and downgrades unverified quotes."""
    slots: list[EvidenceSlot] = [
        extraction.work_authorization,
        extraction.sponsorship,
        extraction.security_clearance,
        extraction.education,
        extraction.graduation_window,
        extraction.experience,
        extraction.location_work_mode,
    ]
    for slot in slots:
        if slot.status == "not_stated":
            slot.quote_verified = False
            slot.evidence_quote = None
        else:
            slot.quote_verified = verify_quote_in_text(slot.evidence_quote, source_text)
            if not slot.quote_verified:
                slot.status = "unclear"

    for item in extraction.other_restrictions:
        item.quote_verified = verify_quote_in_text(item.evidence_quote, source_text)
        if not item.quote_verified:
            item.status = "unclear"

    return extraction


def _find_sentence_with(text: str, pattern: str) -> str | None:
    sentences = re.split(r"(?<=[.!?\n])\s+", text)
    for s in sentences:
        if re.search(pattern, s, flags=re.IGNORECASE):
            return s.strip()
    m = re.search(pattern, text, flags=re.IGNORECASE)
    return m.group(0).strip() if m else None


def _heuristic_mock_extract(description_text: str) -> JobRequirementsExtraction:
    """
    Deterministic extractor used when LLM_PROVIDER=mock (or in unit/integration tests).
    Extracts real verbatim sentences from the posting so quote verification is genuinely exercised.
    """
    extraction = JobRequirementsExtraction()

    # 1. Work authorization / citizenship
    cit_quote = _find_sentence_with(
        description_text, r"\b(?:u\.?s\.?\s+citizen(?:s|ship)?\s+(?:only|is\s+required|required)|must\s+be\s+a\s+u\.?s\.?\s+citizen)\b"
    )
    auth_quote = _find_sentence_with(
        description_text, r"\b(?:authorized\s+to\s+work\s+in\s+the\s+(?:u\.?s\.?|united\s+states))\b"
    )
    if cit_quote:
        extraction.work_authorization = WorkAuthSlot(
            status="required",
            summary="U.S. citizenship is explicitly required.",
            evidence_quote=cit_quote,
            us_citizenship_required=True,
            permanent_residency_accepted=False,
        )
    elif auth_quote:
        extraction.work_authorization = WorkAuthSlot(
            status="required",
            summary="Must be authorized to work in the United States.",
            evidence_quote=auth_quote,
            us_citizenship_required=False,
            permanent_residency_accepted=True,
        )

    # 2. Sponsorship
    no_spons_quote = _find_sentence_with(
        description_text,
        r"\b(?:do\s+not\s+offer\s+(?:visa\s+)?sponsorship|no\s+(?:visa\s+)?sponsorship|without\s+(?:current\s+or\s+future\s+)?(?:visa\s+)?sponsorship|unable\s+to\s+sponsor)\b",
    )
    yes_spons_quote = _find_sentence_with(
        description_text, r"\b(?:visa\s+sponsorship\s+is\s+available|will\s+sponsor\s+visas)\b"
    )
    if no_spons_quote:
        extraction.sponsorship = SponsorshipSlot(
            status="required",
            summary="No visa sponsorship is offered for this role.",
            evidence_quote=no_spons_quote,
            sponsorship_offered="no",
        )
    elif yes_spons_quote:
        extraction.sponsorship = SponsorshipSlot(
            status="preferred",
            summary="Visa sponsorship is available.",
            evidence_quote=yes_spons_quote,
            sponsorship_offered="yes",
        )

    # 3. Security clearance
    cl_quote = _find_sentence_with(
        description_text, r"\b(?:ts/sci|top\s+secret|secret\s+clearance|public\s+trust|security\s+clearance)\b"
    )
    if cl_quote:
        cl_lower = cl_quote.lower()
        level: Any = "secret"
        if "ts/sci" in cl_lower:
            level = "ts_sci"
        elif "top secret" in cl_lower:
            level = "top_secret"
        elif "public trust" in cl_lower:
            level = "public_trust"
        timing: Any = (
            "eligible_to_obtain"
            if re.search(r"\b(?:eligible|ability\s+to\s+obtain|obtain\s+and\s+maintain)\b", cl_lower)
            else "active_required"
        )
        extraction.security_clearance = ClearanceSlot(
            status="required",
            summary=f"Security clearance ({level}, {timing}) stated.",
            evidence_quote=cl_quote,
            level=level,
            timing=timing,
        )

    # 4. Education
    ed_quote = _find_sentence_with(
        description_text, r"\b(?:bachelor(?:'s)?|master(?:'s)?|ph\.?d\.?|bs/ms|degree\s+in)\b"
    )
    if ed_quote:
        ed_lower = ed_quote.lower()
        min_deg: Any = "bachelor"
        if "phd" in ed_lower or "ph.d" in ed_lower:
            min_deg = "phd"
        elif "master" in ed_lower and "bachelor" not in ed_lower and "bs/ms" not in ed_lower:
            min_deg = "master"
        accepts_eq = bool(re.search(r"equivalent\s+(?:\w+\s+)?experience", ed_lower))
        extraction.education = EducationSlot(
            status="required",
            summary=f"Minimum {min_deg} degree stated"
            + (" (or equivalent experience)." if accepts_eq else "."),
            evidence_quote=ed_quote,
            minimum_degree=min_deg,
            preferred_degree="master" if "master" in ed_lower else "none",
            fields_of_study=["Computer Science"] if "computer science" in ed_lower else [],
            accepts_equivalent_experience=accepts_eq,
        )

    # 5. Graduation window
    grad_quote = _find_sentence_with(
        description_text, r"\b(?:graduat(?:e|ed|ing)\s+between|graduat(?:e|ed|ing)\s+in\s+20\d{2}|within\s+the\s+last\s+12\s+months)\b"
    )
    if grad_quote:
        years = re.findall(r"20\d{2}", grad_quote)
        extraction.graduation_window = GraduationWindowSlot(
            status="required",
            summary="Specific graduation window required.",
            evidence_quote=grad_quote,
            earliest_graduation=f"{years[0]}-01" if years else None,
            latest_graduation=f"{years[-1]}-12" if len(years) >= 2 else (f"{years[0]}-12" if years else None),
        )

    # 6. Experience
    exp_quote = _find_sentence_with(
        description_text, r"\b(\d+)\+?\s*years?\s+of\s+(?:[\w\s-]{1,30})?experience\b"
    )
    if exp_quote:
        m = re.search(r"(\d+)\+?\s*years?", exp_quote, flags=re.IGNORECASE)
        yrs = float(m.group(1)) if m else 3.0
        domains = []
        for skill in ("Python", "TypeScript", "React", "FastAPI", "SQL", "AWS", "Docker", "Kubernetes"):
            if re.search(r"\b" + re.escape(skill) + r"\b", description_text, flags=re.IGNORECASE):
                domains.append(skill)
        extraction.experience = ExperienceSlot(
            status="required",
            summary=f"{yrs:g}+ years of experience required.",
            evidence_quote=exp_quote,
            min_years_required=yrs,
            domains=domains,
        )

    # 7. Location / work mode
    loc_quote = _find_sentence_with(
        description_text, r"\b(?:on-?site|in-office|hybrid|remote|reside\s+within)\b"
    )
    if loc_quote:
        loc_lower = loc_quote.lower()
        mode: Any = "remote"
        if "on-site" in loc_lower or "onsite" in loc_lower or "in-office" in loc_lower:
            mode = "onsite"
        elif "hybrid" in loc_lower or "days/week" in loc_lower or "days a week" in loc_lower:
            mode = "hybrid"
        extraction.location_work_mode = LocationSlot(
            status="required",
            summary=f"Work mode stated as {mode}.",
            evidence_quote=loc_quote,
            work_mode=mode,
        )

    # Check contradictions (e.g. both "100% remote" / "remote - us" and "in-office" / "on-site")
    desc_lower = description_text.lower()
    if "remote" in desc_lower and ("in-office" in desc_lower or "reside within" in desc_lower):
        extraction.contradictions_or_ambiguities.append(
            "Posting mentions both Remote and in-office/local residency requirements."
        )

    return extraction


def _build_langchain_model(settings: Settings) -> Any:
    provider = settings.llm_provider
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.openai_api_key,  # type: ignore[arg-type]
            temperature=0.0,
        )
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model_name=settings.llm_model,
            api_key=settings.anthropic_api_key,  # type: ignore[arg-type]
            temperature=0.0,
        )
    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=settings.llm_model,
            google_api_key=settings.google_api_key,
            temperature=0.0,
        )
    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=settings.llm_model,
            base_url=settings.ollama_base_url,
            temperature=0.0,
        )
    raise AnalyzerError("AI_NOT_CONFIGURED", f"Unsupported LLM provider: {provider}", 400)


async def _invoke_llm_structured(
    description_text: str, settings: Settings
) -> JobRequirementsExtraction:
    if settings.llm_provider == "mock":
        return _heuristic_mock_extract(description_text)

    if not settings.is_ai_configured:
        raise AnalyzerError(
            "AI_NOT_CONFIGURED",
            f"LLM provider '{settings.llm_provider}' is not configured with an API key in backend/.env.",
            503,
        )

    model = _build_langchain_model(settings)
    structured_llm = model.with_structured_output(JobRequirementsExtraction)
    messages = [
        ("system", SYSTEM_EXTRACTION_PROMPT),
        ("human", f"Extract eligibility requirements from this job posting:\n\n{description_text[:18000]}"),
    ]
    result = await structured_llm.ainvoke(messages)
    if isinstance(result, JobRequirementsExtraction):
        return result
    return JobRequirementsExtraction.model_validate(result)


def get_cached_analysis(
    db: Session,
    cleaned_text_hash: str | None,
    resume_id: str | None = None,
    settings: Settings | None = None,
) -> AnalyzeJobResponse | None:
    if not cleaned_text_hash:
        return None
    cfg = settings or get_settings()
    stmt = (
        select(RequirementAnalysis)
        .where(
            RequirementAnalysis.cleaned_text_hash == cleaned_text_hash,
            RequirementAnalysis.schema_version == cfg.analysis_schema_version,
        )
        .order_by(RequirementAnalysis.created_at.desc())
    )
    row = db.scalars(stmt).first()
    if not row:
        return None

    extraction = JobRequirementsExtraction.model_validate_json(row.extracted_json)
    comparison = evaluate_extraction_against_profile_and_resumes(db, extraction, resume_id)
    return AnalyzeJobResponse(
        cleaned_text_hash=row.cleaned_text_hash,
        schema_version=row.schema_version,
        model_provider=row.model_provider,
        model_name=row.model_name,
        cached=True,
        latency_ms=row.latency_ms,
        extraction=extraction,
        comparison=comparison,
    )


async def analyze_job_description(
    db: Session,
    description_text: str,
    resume_id: str | None = None,
    force_refresh: bool = False,
    settings: Settings | None = None,
) -> AnalyzeJobResponse:
    cfg = settings or get_settings()
    cleaned_text, cleaned_hash = clean_description_and_hash(description_text)
    if not cleaned_text or not cleaned_hash or len(cleaned_text) < 40:
        raise AnalyzerError(
            "INSUFFICIENT_TEXT",
            "Job description text is too short to analyze (minimum 40 characters).",
            422,
        )

    if not force_refresh:
        cached = get_cached_analysis(db, cleaned_hash, resume_id=resume_id, settings=cfg)
        if cached:
            return cached

    start_ns = time.perf_counter_ns()
    try:
        raw_extraction = await asyncio.wait_for(
            _invoke_llm_structured(description_text, cfg),
            timeout=cfg.llm_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise AnalyzerError(
            "AI_TIMEOUT",
            f"Eligibility analysis timed out after {cfg.llm_timeout_seconds:.0f} seconds. Please retry.",
            504,
        ) from exc
    except AnalyzerError:
        raise
    except Exception as exc:
        raise AnalyzerError(
            "AI_PROVIDER_ERROR",
            f"LLM provider error: {exc}",
            502,
        ) from exc

    latency_ms = int((time.perf_counter_ns() - start_ns) / 1_000_000)
    verified_extraction = validate_extraction_quotes(raw_extraction, description_text)

    # Save to cache
    record = RequirementAnalysis(
        cleaned_text_hash=cleaned_hash,
        schema_version=cfg.analysis_schema_version,
        model_provider=cfg.llm_provider,
        model_name=cfg.llm_model,
        extracted_json=verified_extraction.model_dump_json(),
        latency_ms=latency_ms,
        created_at=utcnow(),
    )
    db.add(record)
    db.commit()

    comparison = evaluate_extraction_against_profile_and_resumes(
        db, verified_extraction, target_resume_id=resume_id
    )
    return AnalyzeJobResponse(
        cleaned_text_hash=cleaned_hash,
        schema_version=cfg.analysis_schema_version,
        model_provider=cfg.llm_provider,
        model_name=cfg.llm_model,
        cached=False,
        latency_ms=latency_ms,
        extraction=verified_extraction,
        comparison=comparison,
    )
