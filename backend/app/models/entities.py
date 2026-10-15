import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid.uuid4())


class Resume(Base):
    """Reusable resume: extracted text in SQLite, PDF bytes in the service data directory."""

    __tablename__ = "resumes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    target_role: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pdf_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    parsed_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    jobs: Mapped[list["Job"]] = relationship("Job", back_populates="resume")


class Job(Base):
    """Canonical job opening and the user's current application state for it."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    company_raw: Mapped[str] = mapped_column(String(300), nullable=False)
    company_norm: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    title_raw: Mapped[str] = mapped_column(String(300), nullable=False)
    title_norm: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    location_raw: Mapped[str | None] = mapped_column(String(300), nullable=True)
    location_norm: Mapped[str | None] = mapped_column(String(300), nullable=True)
    work_mode: Mapped[str | None] = mapped_column(String(50), nullable=True)
    employer_req_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    canonical_ats_url: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)

    status: Mapped[str] = mapped_column(String(50), nullable=False, default="saved", index=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_via_posting_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resume_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True
    )
    confirmation_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    resume: Mapped["Resume | None"] = relationship("Resume", back_populates="jobs")
    postings: Mapped[list["Posting"]] = relationship(
        "Posting", back_populates="job", cascade="all, delete-orphan", order_by="Posting.first_seen_at"
    )
    events: Mapped[list["JobEvent"]] = relationship(
        "JobEvent", back_populates="job", cascade="all, delete-orphan", order_by="JobEvent.created_at.desc()"
    )
    dismissed_duplicates: Mapped[list["DismissedDuplicate"]] = relationship(
        "DismissedDuplicate", back_populates="job", cascade="all, delete-orphan"
    )


class Posting(Base):
    """Observed job posting page on a job board or ATS linked to a canonical Job."""

    __tablename__ = "postings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source_url_raw: Mapped[str] = mapped_column(Text, nullable=False)
    source_url_norm: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    source_posting_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    requisition_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    description_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    cleaned_text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    job: Mapped["Job"] = relationship("Job", back_populates="postings")

    __table_args__ = (
        Index("ix_postings_platform_source_id", "platform", "source_posting_id"),
    )


class JobEvent(Base):
    """Immutable audit log of status changes, notes, and duplicate links/unlinks."""

    __tablename__ = "job_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    old_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    job: Mapped["Job"] = relationship("Job", back_populates="events")


class DismissedDuplicate(Base):
    """Stores user-dismissed duplicate pairs so the matcher never nags twice."""

    __tablename__ = "dismissed_duplicates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dismissed_url_norm: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    dismissed_platform: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dismissed_posting_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    job: Mapped["Job"] = relationship("Job", back_populates="dismissed_duplicates")


class RequirementAnalysis(Base):
    """Cached LLM requirement extraction keyed by cleaned_text_hash."""

    __tablename__ = "requirement_analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    cleaned_text_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    schema_version: Mapped[str] = mapped_column(String(30), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(60), nullable=False)
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    extracted_json: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class UserProfile(Base):
    """Singleton local eligibility profile (id = 1) for deterministic requirement comparison."""

    __tablename__ = "user_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    is_us_citizen: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_permanent_resident: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    authorized_to_work_us: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    requires_visa_sponsorship: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    clearance_level: Mapped[str | None] = mapped_column(String(60), nullable=True)
    highest_degree: Mapped[str | None] = mapped_column(String(60), nullable=True)
    graduation_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    years_experience_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    preferred_work_modes: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_state_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    willing_to_relocate: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    skills_and_domains_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
