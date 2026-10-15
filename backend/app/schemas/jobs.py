from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.analysis import AnalyzeJobResponse

JobStatus = Literal[
    "saved",
    "in_progress",
    "applied",
    "interviewing",
    "offer",
    "rejected",
    "withdrawn",
    "unknown",
]

MatchState = Literal[
    "previously_applied",
    "saved_in_progress",
    "possible_duplicate",
    "no_local_record",
]

MatchTier = Literal["EXACT_MATCH", "STRONG_CANDIDATE", "POSSIBLE_DUPLICATE", "NO_MATCH"]


class PostingInput(BaseModel):
    source_url: str = Field(min_length=1)
    platform: str | None = None
    source_posting_id: str | None = None
    requisition_id: str | None = None
    canonical_ats_url: str | None = None
    company: str = Field(min_length=1)
    title: str = Field(min_length=1)
    location: str | None = None
    work_mode: str | None = None
    description_text: str | None = None


class PostingRead(BaseModel):
    id: str
    job_id: str
    platform: str
    source_url_raw: str
    source_url_norm: str
    source_posting_id: str | None = None
    requisition_id: str | None = None
    cleaned_text_hash: str | None = None
    first_seen_at: datetime
    last_seen_at: datetime


class JobEventRead(BaseModel):
    id: str
    job_id: str
    event_type: str
    old_status: str | None = None
    new_status: str | None = None
    note: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime


class JobRead(BaseModel):
    id: str
    company_raw: str
    company_norm: str
    title_raw: str
    title_norm: str
    location_raw: str | None = None
    location_norm: str | None = None
    work_mode: str | None = None
    employer_req_id: str | None = None
    canonical_ats_url: str | None = None
    status: JobStatus
    applied_at: datetime | None = None
    applied_via_posting_id: str | None = None
    resume_id: str | None = None
    resume_label: str | None = None
    resume_pdf_ref: str | None = None
    confirmation_ref: str | None = None
    created_at: datetime
    updated_at: datetime
    postings: list[PostingRead] = Field(default_factory=list)
    events: list[JobEventRead] = Field(default_factory=list)


class CandidateMatch(BaseModel):
    job: JobRead
    tier: MatchTier
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    conflicting_req_ids: bool = False


class ResolveJobRequest(PostingInput):
    pass


class ResolveJobResponse(BaseModel):
    match_state: MatchState
    match_tier: MatchTier
    matched_job: JobRead | None = None
    candidate_matches: list[CandidateMatch] = Field(default_factory=list)
    normalized_platform: str
    normalized_url: str
    extracted_posting_id: str | None = None
    extracted_req_id: str | None = None
    cleaned_text_hash: str | None = None
    cached_analysis: AnalyzeJobResponse | None = None


class CreateJobRequest(PostingInput):
    status: JobStatus = "saved"
    applied_at: datetime | None = None
    resume_id: str | None = None
    confirmation_ref: str | None = None
    note: str | None = None


class UpdateJobStatusRequest(BaseModel):
    status: JobStatus
    applied_at: datetime | None = None
    resume_id: str | None = None
    confirmation_ref: str | None = None
    note: str | None = None


class AddNoteRequest(BaseModel):
    note: str = Field(min_length=1)


class LinkPostingRequest(PostingInput):
    note: str | None = None


class DismissDuplicateRequest(BaseModel):
    source_url: str
    platform: str | None = None
    source_posting_id: str | None = None
