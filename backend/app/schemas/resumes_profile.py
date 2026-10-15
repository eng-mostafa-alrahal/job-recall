from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ParsedResumeMetadata(BaseModel):
    skills: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    degrees: list[str] = Field(default_factory=list)
    highest_degree_detected: str | None = None
    graduation_date_detected: str | None = None
    years_experience_est: float | None = None
    certifications: list[str] = Field(default_factory=list)


class CreateResumeRequest(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    target_role: str | None = Field(default=None, max_length=200)
    pdf_ref: str | None = Field(
        default=None,
        description="Original PDF filename. The file itself is copied into the local service.",
    )
    content_text: str = Field(min_length=10, description="Full plain text of the resume.")
    is_default: bool = False
    auto_sync_profile: bool = True


class UpdateResumeRequest(BaseModel):
    label: str | None = None
    target_role: str | None = None
    pdf_ref: str | None = None
    content_text: str | None = None
    is_default: bool | None = None
    is_active: bool | None = None
    auto_sync_profile: bool = False


class ResumeRead(BaseModel):
    id: str
    label: str
    target_role: str | None = None
    pdf_ref: str | None = None
    has_stored_pdf: bool = False
    pdf_size_bytes: int | None = None
    content_text: str
    content_hash: str
    parsed: ParsedResumeMetadata
    is_default: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


ClearanceChoice = Literal[
    "none", "public_trust", "secret", "top_secret", "ts_sci", "ts_sci_poly"
]
DegreeChoice = Literal["none", "high_school", "associate", "bachelor", "master", "phd"]
WorkModeChoice = Literal["remote", "hybrid", "onsite"]


class UserProfileRead(BaseModel):
    is_us_citizen: bool | None = None
    is_permanent_resident: bool | None = None
    authorized_to_work_us: bool | None = None
    requires_visa_sponsorship: bool | None = None
    clearance_level: ClearanceChoice | None = None
    highest_degree: DegreeChoice | None = None
    graduation_date: str | None = None
    years_experience_total: float | None = None
    preferred_work_modes: list[WorkModeChoice] = Field(default_factory=list)
    current_state_code: str | None = None
    willing_to_relocate: bool | None = None
    skills_and_domains: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class UserProfileUpdate(BaseModel):
    is_us_citizen: bool | None = None
    is_permanent_resident: bool | None = None
    authorized_to_work_us: bool | None = None
    requires_visa_sponsorship: bool | None = None
    clearance_level: ClearanceChoice | None = None
    highest_degree: DegreeChoice | None = None
    graduation_date: str | None = None
    years_experience_total: float | None = None
    preferred_work_modes: list[WorkModeChoice] | None = None
    current_state_code: str | None = None
    willing_to_relocate: bool | None = None
    skills_and_domains: list[str] | None = None
