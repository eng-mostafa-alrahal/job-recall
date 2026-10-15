from typing import Literal

from pydantic import BaseModel, Field

RequirementLevel = Literal["required", "preferred", "unclear", "not_stated"]
ClearanceLevel = Literal[
    "none", "public_trust", "secret", "top_secret", "ts_sci", "ts_sci_poly", "unspecified"
]
DegreeLevel = Literal[
    "none", "high_school", "associate", "bachelor", "master", "phd", "unspecified"
]
WorkMode = Literal["remote", "hybrid", "onsite", "not_stated"]
ProfileFitLabel = Literal[
    "likely_meets", "possible_mismatch", "needs_review", "profile_missing", "not_applicable"
]


class EvidenceSlot(BaseModel):
    status: RequirementLevel = Field(
        default="not_stated",
        description="Whether this restriction is required, preferred, unclear, or not_stated in the text.",
    )
    summary: str | None = Field(
        default=None,
        description="Concise 1-sentence human summary of the stated rule. Null if not_stated.",
    )
    evidence_quote: str | None = Field(
        default=None,
        description="Exact verbatim substring copied character-for-character from the job description. Null if not_stated.",
    )
    quote_verified: bool = Field(
        default=False,
        description="Populated by Python post-validator after checking substring presence in source text.",
    )


class WorkAuthSlot(EvidenceSlot):
    us_citizenship_required: bool = False
    permanent_residency_accepted: bool | None = None


class SponsorshipSlot(EvidenceSlot):
    sponsorship_offered: Literal["yes", "no", "case_by_case", "not_stated"] = "not_stated"


class ClearanceSlot(EvidenceSlot):
    level: ClearanceLevel = "none"
    timing: Literal["active_required", "eligible_to_obtain", "not_stated"] = "not_stated"


class EducationSlot(EvidenceSlot):
    minimum_degree: DegreeLevel = "none"
    preferred_degree: DegreeLevel = "none"
    fields_of_study: list[str] = Field(default_factory=list)
    accepts_equivalent_experience: bool | None = None


class GraduationWindowSlot(EvidenceSlot):
    earliest_graduation: str | None = Field(default=None, description="YYYY-MM or YYYY if stated")
    latest_graduation: str | None = Field(default=None, description="YYYY-MM or YYYY if stated")


class ExperienceSlot(EvidenceSlot):
    min_years_required: float | None = None
    preferred_years: float | None = None
    domains: list[str] = Field(default_factory=list)


class LocationSlot(EvidenceSlot):
    work_mode: WorkMode = "not_stated"
    restricted_locations: list[str] = Field(default_factory=list)
    relocation_offered: Literal["yes", "no", "not_stated"] = "not_stated"


class OtherRestrictionItem(BaseModel):
    category_label: str = Field(
        description="e.g., License, Certification, Language, Travel, On-Call, Employment Type"
    )
    status: Literal["required", "preferred", "unclear"] = "required"
    summary: str
    evidence_quote: str
    quote_verified: bool = False


class JobRequirementsExtraction(BaseModel):
    work_authorization: WorkAuthSlot = Field(default_factory=WorkAuthSlot)
    sponsorship: SponsorshipSlot = Field(default_factory=SponsorshipSlot)
    security_clearance: ClearanceSlot = Field(default_factory=ClearanceSlot)
    education: EducationSlot = Field(default_factory=EducationSlot)
    graduation_window: GraduationWindowSlot = Field(default_factory=GraduationWindowSlot)
    experience: ExperienceSlot = Field(default_factory=ExperienceSlot)
    location_work_mode: LocationSlot = Field(default_factory=LocationSlot)
    other_restrictions: list[OtherRestrictionItem] = Field(default_factory=list)
    contradictions_or_ambiguities: list[str] = Field(
        default_factory=list,
        description="Note any internal contradictions in the posting (e.g., 'Title says Remote but body says 3 days/week in NYC').",
    )


class CategoryEvaluation(BaseModel):
    category: str
    fit_label: ProfileFitLabel
    reason: str


class ResumeFitRecommendation(BaseModel):
    recommended_resume_id: str | None = None
    recommended_resume_label: str | None = None
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    coverage_ratio: float | None = None
    note: str | None = None


class ProfileAndResumeComparison(BaseModel):
    overall_fit: ProfileFitLabel = "not_applicable"
    evaluations: dict[str, CategoryEvaluation] = Field(default_factory=dict)
    resume_recommendation: ResumeFitRecommendation = Field(default_factory=ResumeFitRecommendation)


class AnalyzeJobRequest(BaseModel):
    description_text: str = Field(min_length=1)
    resume_id: str | None = None
    force_refresh: bool = False


class AnalyzeJobResponse(BaseModel):
    cleaned_text_hash: str
    schema_version: str
    model_provider: str
    model_name: str
    cached: bool
    latency_ms: int
    extraction: JobRequirementsExtraction
    comparison: ProfileAndResumeComparison
