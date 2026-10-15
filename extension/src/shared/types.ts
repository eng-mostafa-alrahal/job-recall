export type JobStatus =
  | "saved"
  | "in_progress"
  | "applied"
  | "interviewing"
  | "offer"
  | "rejected"
  | "withdrawn"
  | "unknown";

export type MatchState =
  | "previously_applied"
  | "saved_in_progress"
  | "possible_duplicate"
  | "no_local_record";

export type MatchTier =
  | "EXACT_MATCH"
  | "STRONG_CANDIDATE"
  | "POSSIBLE_DUPLICATE"
  | "NO_MATCH";

export type RequirementLevel = "required" | "preferred" | "unclear" | "not_stated";
export type ProfileFitLabel =
  | "likely_meets"
  | "possible_mismatch"
  | "needs_review"
  | "profile_missing"
  | "not_applicable";

export interface ExtractedPagePosting {
  source_url: string;
  platform?: string | null;
  source_posting_id?: string | null;
  requisition_id?: string | null;
  canonical_ats_url?: string | null;
  company: string;
  title: string;
  location?: string | null;
  work_mode?: string | null;
  description_text?: string | null;
}

export interface PostingRead {
  id: string;
  job_id: string;
  platform: string;
  source_url_raw: string;
  source_url_norm: string;
  source_posting_id: string | null;
  requisition_id: string | null;
  cleaned_text_hash: string | null;
  first_seen_at: string;
  last_seen_at: string;
}

export interface JobEventRead {
  id: string;
  job_id: string;
  event_type: string;
  old_status: string | null;
  new_status: string | null;
  note: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface JobRead {
  id: string;
  company_raw: string;
  company_norm: string;
  title_raw: string;
  title_norm: string;
  location_raw: string | null;
  location_norm: string | null;
  work_mode: string | null;
  employer_req_id: string | null;
  canonical_ats_url: string | null;
  status: JobStatus;
  applied_at: string | null;
  applied_via_posting_id: string | null;
  resume_id: string | null;
  resume_label: string | null;
  resume_pdf_ref: string | null;
  confirmation_ref: string | null;
  created_at: string;
  updated_at: string;
  postings: PostingRead[];
  events: JobEventRead[];
}

export interface CandidateMatch {
  job: JobRead;
  tier: MatchTier;
  confidence: number;
  reasons: string[];
  conflicting_req_ids: boolean;
}

export interface EvidenceSlot {
  status: RequirementLevel;
  summary: string | null;
  evidence_quote: string | null;
  quote_verified: boolean;
  [key: string]: unknown;
}

export interface OtherRestrictionItem {
  category_label: string;
  status: "required" | "preferred" | "unclear";
  summary: string;
  evidence_quote: string;
  quote_verified: boolean;
}

export interface JobRequirementsExtraction {
  work_authorization: EvidenceSlot & {
    us_citizenship_required: boolean;
    permanent_residency_accepted: boolean | null;
  };
  sponsorship: EvidenceSlot & {
    sponsorship_offered: "yes" | "no" | "case_by_case" | "not_stated";
  };
  security_clearance: EvidenceSlot & {
    level: string;
    timing: string;
  };
  education: EvidenceSlot & {
    minimum_degree: string;
    preferred_degree: string;
    fields_of_study: string[];
    accepts_equivalent_experience: boolean | null;
  };
  graduation_window: EvidenceSlot & {
    earliest_graduation: string | null;
    latest_graduation: string | null;
  };
  experience: EvidenceSlot & {
    min_years_required: number | null;
    preferred_years: number | null;
    domains: string[];
  };
  location_work_mode: EvidenceSlot & {
    work_mode: string;
    restricted_locations: string[];
    relocation_offered: string;
  };
  other_restrictions: OtherRestrictionItem[];
  contradictions_or_ambiguities: string[];
}

export interface CategoryEvaluation {
  category: string;
  fit_label: ProfileFitLabel;
  reason: string;
}

export interface ResumeFitRecommendation {
  recommended_resume_id: string | null;
  recommended_resume_label: string | null;
  matched_skills: string[];
  missing_skills: string[];
  coverage_ratio: number | null;
  note: string | null;
}

export interface ProfileAndResumeComparison {
  overall_fit: ProfileFitLabel;
  evaluations: Record<string, CategoryEvaluation>;
  resume_recommendation: ResumeFitRecommendation;
}

export interface AnalyzeJobResponse {
  cleaned_text_hash: string;
  schema_version: string;
  model_provider: string;
  model_name: string;
  cached: boolean;
  latency_ms: number;
  extraction: JobRequirementsExtraction;
  comparison: ProfileAndResumeComparison;
}

export interface ResolveJobResponse {
  match_state: MatchState;
  match_tier: MatchTier;
  matched_job: JobRead | null;
  candidate_matches: CandidateMatch[];
  normalized_platform: string;
  normalized_url: string;
  extracted_posting_id: string | null;
  extracted_req_id: string | null;
  cleaned_text_hash: string | null;
  cached_analysis: AnalyzeJobResponse | null;
}

export interface ParsedResumeMetadata {
  skills: string[];
  domains: string[];
  degrees: string[];
  highest_degree_detected: string | null;
  graduation_date_detected: string | null;
  years_experience_est: number | null;
  certifications: string[];
}

export interface ResumeRead {
  id: string;
  label: string;
  target_role: string | null;
  pdf_ref: string | null;
  has_stored_pdf: boolean;
  pdf_size_bytes: number | null;
  content_text: string;
  content_hash: string;
  parsed: ParsedResumeMetadata;
  is_default: boolean;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface UserProfileRead {
  is_us_citizen: boolean | null;
  is_permanent_resident: boolean | null;
  authorized_to_work_us: boolean | null;
  requires_visa_sponsorship: boolean | null;
  clearance_level: string | null;
  highest_degree: string | null;
  graduation_date: string | null;
  years_experience_total: number | null;
  preferred_work_modes: string[];
  current_state_code: string | null;
  willing_to_relocate: boolean | null;
  skills_and_domains: string[];
  updated_at: string | null;
}

export interface ExtensionSettings {
  apiBaseUrl: string;
  apiToken: string;
  aiConsentAccepted: boolean;
  autoAnalyze: boolean;
}

export const DEFAULT_SETTINGS: ExtensionSettings = {
  apiBaseUrl: "http://127.0.0.1:8765",
  apiToken: "jobrecall-local-dev-token-2026",
  aiConsentAccepted: false,
  autoAnalyze: false,
};
