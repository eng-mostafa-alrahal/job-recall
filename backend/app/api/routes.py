import json
import secrets
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import verify_api_token
from app.config import Settings, get_settings
from app.database import get_db
from app.models.entities import (
    DismissedDuplicate,
    Job,
    JobEvent,
    Posting,
    Resume,
    UserProfile,
    utcnow,
)
from app.schemas.analysis import AnalyzeJobRequest, AnalyzeJobResponse
from app.schemas.jobs import (
    AddNoteRequest,
    CandidateMatch,
    CreateJobRequest,
    DismissDuplicateRequest,
    JobEventRead,
    JobRead,
    LinkPostingRequest,
    PostingRead,
    ResolveJobRequest,
    ResolveJobResponse,
    UpdateJobStatusRequest,
)
from app.schemas.resumes_profile import (
    CreateResumeRequest,
    ParsedResumeMetadata,
    ResumeRead,
    UpdateResumeRequest,
    UserProfileRead,
    UserProfileUpdate,
)
from app.services.analyzer import analyze_job_description, get_cached_analysis
from app.services.matcher import resolve_posting_against_db
from app.services.normalizer import normalize_posting_input
from app.services.resume_parser import (
    compute_resume_hash,
    extract_text_from_pdf_bytes,
    parse_resume_text,
    sync_parsed_resume_to_profile,
)
from app.services.resume_store import (
    MAX_RESUME_PDF_BYTES,
    delete_resume_pdf,
    resume_pdf_info,
    safe_download_filename,
    save_resume_pdf,
    stored_pdf_path,
)

router = APIRouter(prefix="/api/v1")


def _serialize_job(job: Job) -> JobRead:
    postings_out = [
        PostingRead(
            id=p.id,
            job_id=p.job_id,
            platform=p.platform,
            source_url_raw=p.source_url_raw,
            source_url_norm=p.source_url_norm,
            source_posting_id=p.source_posting_id,
            requisition_id=p.requisition_id,
            cleaned_text_hash=p.cleaned_text_hash,
            first_seen_at=p.first_seen_at,
            last_seen_at=p.last_seen_at,
        )
        for p in job.postings
    ]
    events_out = []
    for e in job.events:
        meta = None
        if e.metadata_json:
            try:
                meta = json.loads(e.metadata_json)
            except ValueError:
                meta = None
        events_out.append(
            JobEventRead(
                id=e.id,
                job_id=e.job_id,
                event_type=e.event_type,
                old_status=e.old_status,
                new_status=e.new_status,
                note=e.note,
                metadata=meta,
                created_at=e.created_at,
            )
        )
    return JobRead(
        id=job.id,
        company_raw=job.company_raw,
        company_norm=job.company_norm,
        title_raw=job.title_raw,
        title_norm=job.title_norm,
        location_raw=job.location_raw,
        location_norm=job.location_norm,
        work_mode=job.work_mode,
        employer_req_id=job.employer_req_id,
        canonical_ats_url=job.canonical_ats_url,
        status=job.status,  # type: ignore[arg-type]
        applied_at=job.applied_at,
        applied_via_posting_id=job.applied_via_posting_id,
        resume_id=job.resume_id,
        resume_label=job.resume.label if job.resume else None,
        resume_pdf_ref=job.resume.pdf_ref if job.resume else None,
        confirmation_ref=job.confirmation_ref,
        created_at=job.created_at,
        updated_at=job.updated_at,
        postings=postings_out,
        events=events_out,
    )


def _serialize_resume(res: Resume, settings: Settings) -> ResumeRead:
    parsed = ParsedResumeMetadata()
    if res.parsed_json:
        try:
            parsed = ParsedResumeMetadata.model_validate_json(res.parsed_json)
        except ValueError:
            parsed = ParsedResumeMetadata()
    has_pdf, pdf_size = resume_pdf_info(res.id, settings)
    return ResumeRead(
        id=res.id,
        label=res.label,
        target_role=res.target_role,
        pdf_ref=res.pdf_ref,
        has_stored_pdf=has_pdf,
        pdf_size_bytes=pdf_size,
        content_text=res.content_text,
        content_hash=res.content_hash,
        parsed=parsed,
        is_default=res.is_default,
        is_active=res.is_active,
        created_at=res.created_at,
        updated_at=res.updated_at,
    )


def _serialize_profile(profile: UserProfile | None) -> UserProfileRead:
    if not profile:
        return UserProfileRead()
    pref_modes = []
    if profile.preferred_work_modes:
        try:
            pref_modes = json.loads(profile.preferred_work_modes)
        except ValueError:
            pref_modes = []
    skills = []
    if profile.skills_and_domains_json:
        try:
            skills = json.loads(profile.skills_and_domains_json)
        except ValueError:
            skills = []
    return UserProfileRead(
        is_us_citizen=profile.is_us_citizen,
        is_permanent_resident=profile.is_permanent_resident,
        authorized_to_work_us=profile.authorized_to_work_us,
        requires_visa_sponsorship=profile.requires_visa_sponsorship,
        clearance_level=profile.clearance_level,  # type: ignore[arg-type]
        highest_degree=profile.highest_degree,  # type: ignore[arg-type]
        graduation_date=profile.graduation_date,
        years_experience_total=profile.years_experience_total,
        preferred_work_modes=pref_modes,
        current_state_code=profile.current_state_code,
        willing_to_relocate=profile.willing_to_relocate,
        skills_and_domains=skills,
        updated_at=profile.updated_at,
    )


def _load_job_or_404(db: Session, job_id: str) -> Job:
    stmt = (
        select(Job)
        .where(Job.id == job_id)
        .options(
            selectinload(Job.postings),
            selectinload(Job.events),
            selectinload(Job.resume),
            selectinload(Job.dismissed_duplicates),
        )
        .execution_options(populate_existing=True)
    )
    job = db.scalars(stmt).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": f"Job {job_id} not found."},
        )
    return job


# --- HEALTH ENDPOINT ---

@router.get("/health")
def health_check(
    x_jobrecall_token: str | None = Header(default=None, alias="X-JobRecall-Token"),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    auth_valid = bool(
        not settings.jobrecall_api_token
        or (
            x_jobrecall_token
            and secrets.compare_digest(x_jobrecall_token, settings.jobrecall_api_token)
        )
    )
    return {
        "status": "ok",
        "version": "0.1.0",
        "auth_valid": auth_valid,
        "ai_configured": settings.is_ai_configured,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
    }


# --- JOB RESOLUTION & TRACKING ENDPOINTS ---

@router.post("/jobs/resolve", response_model=ResolveJobResponse, dependencies=[Depends(verify_api_token)])
def resolve_job(
    payload: ResolveJobRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ResolveJobResponse:
    norm = normalize_posting_input(
        source_url=payload.source_url,
        company=payload.company,
        title=payload.title,
        location=payload.location,
        work_mode=payload.work_mode,
        platform=payload.platform,
        source_posting_id=payload.source_posting_id,
        requisition_id=payload.requisition_id,
        canonical_ats_url=payload.canonical_ats_url,
        description_text=payload.description_text,
    )
    outcome = resolve_posting_against_db(db, norm)

    cached_analysis = get_cached_analysis(
        db,
        norm.cleaned_text_hash,
        resume_id=outcome.matched_job.resume_id if outcome.matched_job else None,
        settings=settings,
    )

    return ResolveJobResponse(
        match_state=outcome.match_state,
        match_tier=outcome.match_tier,
        matched_job=_serialize_job(outcome.matched_job) if outcome.matched_job else None,
        candidate_matches=[
            CandidateMatch(
                job=_serialize_job(c.job),
                tier=c.tier,
                confidence=c.confidence,
                reasons=c.reasons,
                conflicting_req_ids=c.conflicting_req_ids,
            )
            for c in outcome.candidates
        ],
        normalized_platform=norm.platform,
        normalized_url=norm.source_url_norm,
        extracted_posting_id=norm.source_posting_id,
        extracted_req_id=norm.requisition_id,
        cleaned_text_hash=norm.cleaned_text_hash,
        cached_analysis=cached_analysis,
    )


@router.post(
    "/jobs",
    response_model=JobRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_api_token)],
)
def create_or_save_job(
    payload: CreateJobRequest,
    db: Session = Depends(get_db),
) -> JobRead:
    norm = normalize_posting_input(
        source_url=payload.source_url,
        company=payload.company,
        title=payload.title,
        location=payload.location,
        work_mode=payload.work_mode,
        platform=payload.platform,
        source_posting_id=payload.source_posting_id,
        requisition_id=payload.requisition_id,
        canonical_ats_url=payload.canonical_ats_url,
        description_text=payload.description_text,
    )

    now = utcnow()
    applied_at = payload.applied_at or (now if payload.status == "applied" else None)

    job = Job(
        company_raw=payload.company.strip(),
        company_norm=norm.company_norm,
        title_raw=payload.title.strip(),
        title_norm=norm.title_norm,
        location_raw=payload.location.strip() if payload.location else None,
        location_norm=norm.location_norm,
        work_mode=norm.work_mode,
        employer_req_id=norm.requisition_id,
        canonical_ats_url=norm.canonical_ats_url,
        status=payload.status,
        applied_at=applied_at,
        resume_id=payload.resume_id,
        confirmation_ref=payload.confirmation_ref,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    db.flush()

    posting = Posting(
        job_id=job.id,
        platform=norm.platform,
        source_url_raw=payload.source_url.strip(),
        source_url_norm=norm.source_url_norm,
        source_posting_id=norm.source_posting_id,
        requisition_id=norm.requisition_id,
        description_raw=payload.description_text,
        cleaned_text_hash=norm.cleaned_text_hash,
        first_seen_at=now,
        last_seen_at=now,
    )
    db.add(posting)
    db.flush()

    if payload.status == "applied":
        job.applied_via_posting_id = posting.id

    event = JobEvent(
        job_id=job.id,
        event_type="created",
        old_status=None,
        new_status=payload.status,
        note=payload.note or f"Job captured from {norm.platform} with status '{payload.status}'.",
        metadata_json=json.dumps({"posting_id": posting.id, "platform": norm.platform}),
        created_at=now,
    )
    db.add(event)
    db.commit()

    return _serialize_job(_load_job_or_404(db, job.id))


@router.get("/jobs", response_model=list[JobRead], dependencies=[Depends(verify_api_token)])
def list_jobs(
    status_filter: str | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[JobRead]:
    stmt = (
        select(Job)
        .options(
            selectinload(Job.postings),
            selectinload(Job.events),
            selectinload(Job.resume),
        )
        .order_by(Job.updated_at.desc())
    )
    if status_filter:
        stmt = stmt.where(Job.status == status_filter)
    if q and q.strip():
        pattern = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                Job.company_norm.like(pattern),
                Job.title_norm.like(pattern),
                Job.company_raw.ilike(pattern),
                Job.title_raw.ilike(pattern),
                Job.employer_req_id.ilike(pattern),
            )
        )
    stmt = stmt.limit(limit).offset(offset)
    jobs = db.scalars(stmt).all()
    return [_serialize_job(j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=JobRead, dependencies=[Depends(verify_api_token)])
def get_job(job_id: str, db: Session = Depends(get_db)) -> JobRead:
    job = _load_job_or_404(db, job_id)
    return _serialize_job(job)


@router.delete(
    "/jobs/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_api_token)],
)
def delete_job(job_id: str, db: Session = Depends(get_db)) -> None:
    job = _load_job_or_404(db, job_id)
    db.delete(job)
    db.commit()


@router.patch(
    "/jobs/{job_id}/status",
    response_model=JobRead,
    dependencies=[Depends(verify_api_token)],
)
def update_job_status(
    job_id: str,
    payload: UpdateJobStatusRequest,
    db: Session = Depends(get_db),
) -> JobRead:
    job = _load_job_or_404(db, job_id)
    old_status = job.status
    now = utcnow()

    job.status = payload.status
    if payload.status == "applied" and not job.applied_at:
        job.applied_at = payload.applied_at or now
    elif payload.applied_at is not None:
        job.applied_at = payload.applied_at

    if payload.resume_id is not None:
        job.resume_id = payload.resume_id
    if payload.confirmation_ref is not None:
        job.confirmation_ref = payload.confirmation_ref
    job.updated_at = now

    event = JobEvent(
        job_id=job.id,
        event_type="status_changed",
        old_status=old_status,
        new_status=payload.status,
        note=payload.note or f"Status changed from '{old_status}' to '{payload.status}'.",
        metadata_json=json.dumps(
            {
                "resume_id": job.resume_id,
                "confirmation_ref": job.confirmation_ref,
            }
        ),
        created_at=now,
    )
    db.add(event)
    db.commit()

    return _serialize_job(_load_job_or_404(db, job.id))


@router.post(
    "/jobs/{job_id}/notes",
    response_model=JobRead,
    dependencies=[Depends(verify_api_token)],
)
def add_job_note(
    job_id: str,
    payload: AddNoteRequest,
    db: Session = Depends(get_db),
) -> JobRead:
    job = _load_job_or_404(db, job_id)
    now = utcnow()
    job.updated_at = now
    event = JobEvent(
        job_id=job.id,
        event_type="note_added",
        old_status=job.status,
        new_status=job.status,
        note=payload.note.strip(),
        created_at=now,
    )
    db.add(event)
    db.commit()
    return _serialize_job(_load_job_or_404(db, job.id))


@router.post(
    "/jobs/{job_id}/link-posting",
    response_model=JobRead,
    dependencies=[Depends(verify_api_token)],
)
def link_posting_to_job(
    job_id: str,
    payload: LinkPostingRequest,
    db: Session = Depends(get_db),
) -> JobRead:
    job = _load_job_or_404(db, job_id)
    norm = normalize_posting_input(
        source_url=payload.source_url,
        company=payload.company,
        title=payload.title,
        location=payload.location,
        work_mode=payload.work_mode,
        platform=payload.platform,
        source_posting_id=payload.source_posting_id,
        requisition_id=payload.requisition_id,
        canonical_ats_url=payload.canonical_ats_url,
        description_text=payload.description_text,
    )
    now = utcnow()

    # Check if this posting URL is already attached
    for existing in job.postings:
        if existing.source_url_norm == norm.source_url_norm or (
            norm.source_posting_id
            and existing.platform == norm.platform
            and existing.source_posting_id == norm.source_posting_id
        ):
            existing.last_seen_at = now
            db.commit()
            return _serialize_job(_load_job_or_404(db, job.id))

    posting = Posting(
        job_id=job.id,
        platform=norm.platform,
        source_url_raw=payload.source_url.strip(),
        source_url_norm=norm.source_url_norm,
        source_posting_id=norm.source_posting_id,
        requisition_id=norm.requisition_id,
        description_raw=payload.description_text,
        cleaned_text_hash=norm.cleaned_text_hash,
        first_seen_at=now,
        last_seen_at=now,
    )
    db.add(posting)
    if norm.requisition_id and not job.employer_req_id:
        job.employer_req_id = norm.requisition_id
    if norm.canonical_ats_url and not job.canonical_ats_url:
        job.canonical_ats_url = norm.canonical_ats_url
    job.updated_at = now
    db.flush()

    event = JobEvent(
        job_id=job.id,
        event_type="posting_linked",
        old_status=job.status,
        new_status=job.status,
        note=payload.note or f"Linked duplicate posting from {norm.platform} ({norm.source_url_norm}).",
        metadata_json=json.dumps({"posting_id": posting.id, "platform": norm.platform}),
        created_at=now,
    )
    db.add(event)
    db.commit()
    return _serialize_job(_load_job_or_404(db, job.id))


@router.post(
    "/jobs/{job_id}/unlink-posting/{posting_id}",
    response_model=JobRead,
    dependencies=[Depends(verify_api_token)],
)
def unlink_posting_into_separate_job(
    job_id: str,
    posting_id: str,
    db: Session = Depends(get_db),
) -> JobRead:
    """Reversibly separates a linked posting into its own standalone canonical Job."""
    job = _load_job_or_404(db, job_id)
    if len(job.postings) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "CANNOT_UNLINK_ONLY_POSTING",
                "message": "Cannot unlink the only posting on a job.",
            },
        )
    target_posting = next((p for p in job.postings if p.id == posting_id), None)
    if not target_posting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "POSTING_NOT_FOUND", "message": "Posting not found on this job."},
        )

    now = utcnow()
    new_job = Job(
        company_raw=job.company_raw,
        company_norm=job.company_norm,
        title_raw=job.title_raw,
        title_norm=job.title_norm,
        location_raw=job.location_raw,
        location_norm=job.location_norm,
        work_mode=job.work_mode,
        employer_req_id=target_posting.requisition_id,
        status="saved",
        created_at=now,
        updated_at=now,
    )
    db.add(new_job)
    db.flush()

    target_posting.job_id = new_job.id
    db.add(
        JobEvent(
            job_id=job.id,
            event_type="posting_unlinked",
            old_status=job.status,
            new_status=job.status,
            note=f"Unlinked posting ({target_posting.platform}) into separate job {new_job.id}.",
            created_at=now,
        )
    )
    db.add(
        JobEvent(
            job_id=new_job.id,
            event_type="created",
            old_status=None,
            new_status="saved",
            note=f"Split from job {job.id}.",
            created_at=now,
        )
    )
    db.commit()
    return _serialize_job(_load_job_or_404(db, job.id))


@router.post(
    "/jobs/{job_id}/dismiss-duplicate",
    response_model=JobRead,
    dependencies=[Depends(verify_api_token)],
)
def dismiss_duplicate_suggestion(
    job_id: str,
    payload: DismissDuplicateRequest,
    db: Session = Depends(get_db),
) -> JobRead:
    job = _load_job_or_404(db, job_id)
    norm = normalize_posting_input(
        source_url=payload.source_url,
        company=job.company_raw,
        title=job.title_raw,
        platform=payload.platform,
        source_posting_id=payload.source_posting_id,
    )
    dismissed = DismissedDuplicate(
        job_id=job.id,
        dismissed_url_norm=norm.source_url_norm,
        dismissed_platform=norm.platform,
        dismissed_posting_id=norm.source_posting_id,
        created_at=utcnow(),
    )
    db.add(dismissed)
    db.commit()
    return _serialize_job(_load_job_or_404(db, job.id))


# --- AI REQUIREMENT ANALYSIS ENDPOINT ---

@router.post(
    "/jobs/analyze",
    response_model=AnalyzeJobResponse,
    dependencies=[Depends(verify_api_token)],
)
async def analyze_job_endpoint(
    payload: AnalyzeJobRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AnalyzeJobResponse:
    return await analyze_job_description(
        db=db,
        description_text=payload.description_text,
        resume_id=payload.resume_id,
        force_refresh=payload.force_refresh,
        settings=settings,
    )


# --- RESUME (TEXT + PDF REFERENCE) ENDPOINTS ---

@router.get("/resumes", response_model=list[ResumeRead], dependencies=[Depends(verify_api_token)])
def list_resumes(
    include_inactive: bool = Query(default=False),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[ResumeRead]:
    stmt = select(Resume).order_by(Resume.is_default.desc(), Resume.updated_at.desc())
    if not include_inactive:
        stmt = stmt.where(Resume.is_active.is_(True))
    rows = db.scalars(stmt).all()
    return [_serialize_resume(r, settings) for r in rows]


@router.post(
    "/resumes",
    response_model=ResumeRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_api_token)],
)
def create_resume(
    payload: CreateResumeRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ResumeRead:
    now = utcnow()
    content_hash = compute_resume_hash(payload.content_text)
    parsed = parse_resume_text(payload.content_text)

    if payload.is_default:
        for existing in db.scalars(select(Resume).where(Resume.is_default.is_(True))).all():
            existing.is_default = False

    # If this is the very first active resume, default it automatically
    active_count = len(db.scalars(select(Resume).where(Resume.is_active.is_(True))).all())
    make_default = payload.is_default or (active_count == 0)

    res = Resume(
        label=payload.label.strip(),
        target_role=payload.target_role.strip() if payload.target_role else None,
        pdf_ref=payload.pdf_ref.strip() if payload.pdf_ref else None,
        content_text=payload.content_text.strip(),
        content_hash=content_hash,
        parsed_json=parsed.model_dump_json(),
        is_default=make_default,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(res)
    db.commit()
    db.refresh(res)

    if payload.auto_sync_profile:
        sync_parsed_resume_to_profile(db, parsed)

    return _serialize_resume(res, settings)


@router.post(
    "/resumes/extract-pdf",
    response_model=ResumeRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_api_token)],
)
async def create_resume_from_pdf(
    file: UploadFile = File(...),
    label: str = Form(...),
    target_role: str | None = Form(default=None),
    pdf_ref: str | None = Form(default=None),
    content_text: str | None = Form(default=None),
    is_default: bool = Form(default=False),
    auto_sync_profile: bool = Form(default=True),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ResumeRead:
    """
    Copies the PDF into the local service data directory and stores extracted text in SQLite.
    The stored copy remains available if the original file is moved or deleted.
    """
    raw_bytes = await file.read()
    if len(raw_bytes) > MAX_RESUME_PDF_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "PDF_TOO_LARGE", "message": "PDF must be 15 MB or smaller."},
        )

    provided_text = (content_text or "").strip()
    if len(provided_text) >= 10:
        extracted_text = provided_text
    else:
        try:
            extracted_text = extract_text_from_pdf_bytes(raw_bytes)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "PDF_PARSE_ERROR", "message": f"Failed to extract text from PDF: {exc}"},
            ) from exc

    if len(extracted_text.strip()) < 20:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "PDF_EMPTY_TEXT",
                "message": "Could not extract readable text from PDF. Paste the resume text directly.",
            },
        )

    original_name = (pdf_ref or file.filename or "resume.pdf").replace("\\", "/").split("/")[-1]
    req = CreateResumeRequest(
        label=label,
        target_role=target_role,
        pdf_ref=original_name,
        content_text=extracted_text,
        is_default=is_default,
        auto_sync_profile=auto_sync_profile,
    )
    created = create_resume(req, db, settings)
    try:
        save_resume_pdf(created.id, raw_bytes, settings)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "PDF_STORE_ERROR", "message": str(exc)},
        ) from exc
    stored = db.get(Resume, created.id)
    if not stored:
        return created
    return _serialize_resume(stored, settings)


@router.patch(
    "/resumes/{resume_id}",
    response_model=ResumeRead,
    dependencies=[Depends(verify_api_token)],
)
def update_resume(
    resume_id: str,
    payload: UpdateResumeRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ResumeRead:
    res = db.get(Resume, resume_id)
    if not res:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RESUME_NOT_FOUND", "message": f"Resume {resume_id} not found."},
        )

    if payload.label is not None:
        res.label = payload.label.strip()
    if payload.target_role is not None:
        res.target_role = payload.target_role.strip() or None
    if payload.pdf_ref is not None:
        res.pdf_ref = payload.pdf_ref.strip() or None
    if payload.content_text is not None:
        res.content_text = payload.content_text.strip()
        res.content_hash = compute_resume_hash(res.content_text)
        parsed = parse_resume_text(res.content_text)
        res.parsed_json = parsed.model_dump_json()
        if payload.auto_sync_profile:
            sync_parsed_resume_to_profile(db, parsed)
    if payload.is_default is True:
        for other in db.scalars(select(Resume).where(Resume.id != res.id)).all():
            other.is_default = False
        res.is_default = True
    elif payload.is_default is False:
        res.is_default = False

    if payload.is_active is not None:
        res.is_active = payload.is_active

    res.updated_at = utcnow()
    db.commit()
    db.refresh(res)
    return _serialize_resume(res, settings)


@router.get(
    "/resumes/{resume_id}/pdf",
    dependencies=[Depends(verify_api_token)],
)
def download_resume_pdf(
    resume_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    res = db.get(Resume, resume_id)
    if not res:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RESUME_NOT_FOUND", "message": f"Resume {resume_id} not found."},
        )
    path = stored_pdf_path(resume_id, settings)
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "PDF_NOT_STORED",
                "message": "This resume has no PDF copy in the local service. Upload the PDF again.",
            },
        )
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=safe_download_filename(res.pdf_ref, res.label),
    )


@router.delete(
    "/resumes/{resume_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_api_token)],
)
def delete_resume(
    resume_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    res = db.get(Resume, resume_id)
    if not res:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RESUME_NOT_FOUND", "message": f"Resume {resume_id} not found."},
        )
    delete_resume_pdf(resume_id, settings)
    db.delete(res)
    db.commit()


# --- LOCAL USER PROFILE ENDPOINTS ---

@router.get("/profile", response_model=UserProfileRead, dependencies=[Depends(verify_api_token)])
def get_profile(db: Session = Depends(get_db)) -> UserProfileRead:
    profile = db.get(UserProfile, 1)
    return _serialize_profile(profile)


@router.put("/profile", response_model=UserProfileRead, dependencies=[Depends(verify_api_token)])
def update_profile(
    payload: UserProfileUpdate,
    db: Session = Depends(get_db),
) -> UserProfileRead:
    profile = db.get(UserProfile, 1)
    now = utcnow()
    if not profile:
        profile = UserProfile(id=1, updated_at=now)
        db.add(profile)

    update_data = payload.model_dump(exclude_unset=True)
    if "preferred_work_modes" in update_data:
        modes = update_data.pop("preferred_work_modes")
        profile.preferred_work_modes = json.dumps(modes) if modes is not None else None
    if "skills_and_domains" in update_data:
        skills = update_data.pop("skills_and_domains")
        profile.skills_and_domains_json = json.dumps(skills) if skills is not None else None

    for field_name, val in update_data.items():
        setattr(profile, field_name, val)

    profile.updated_at = now
    db.commit()
    db.refresh(profile)
    return _serialize_profile(profile)


# --- PORTABLE DATA EXPORT & IMPORT ---

@router.get("/export", dependencies=[Depends(verify_api_token)])
def export_all_data(db: Session = Depends(get_db)) -> dict[str, Any]:
    jobs = list_jobs(status_filter=None, q=None, limit=200, offset=0, db=db)
    resumes = list_resumes(include_inactive=True, db=db)
    profile = get_profile(db=db)
    return {
        "schema_version": "1.0",
        "exported_at": utcnow().isoformat(),
        "profile": profile.model_dump(mode="json"),
        "resumes": [r.model_dump(mode="json") for r in resumes],
        "jobs": [j.model_dump(mode="json") for j in jobs],
    }
