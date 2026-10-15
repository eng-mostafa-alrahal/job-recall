# JobRecall — System Architecture, Data Model & Schemas

This document defines the technical architecture, database schema, normalization and matching algorithms, API contracts, and LangChain structured-output schemas for **JobRecall**.

---

## 1. System Architecture Diagram

```text
+-----------------------------------------------------------------------------------+
| CHROME BROWSER (Manifest V3 Extension)                                            |
|                                                                                   |
|  +-------------------------------+         +-----------------------------------+  |
|  | Job Page Tab (Content Script) |         | Side Panel UI (TypeScript / HTML) |  |
|  | - Site Adapters (LinkedIn,    |         | - Instant Match / History Card    |  |
|  |   Indeed, Greenhouse, Workday)|         | - Save / Mark Applied / Notes     |  |
|  | - SPA URL/DOM Observer        |         | - Possible Duplicate Linker       |  |
|  | - Manual Selection/Paste      |         | - Eligibility & Profile Cards     |  |
|  +---------------+---------------+         +-----------------+-----------------+  |
|                  | chrome.runtime.sendMessage                ^                    |
|                  v                                           |                    |
|  +-----------------------------------------------------------+-----------------+  |
|  | Background Service Worker (background.ts)                                   |  |
|  | - Tracks active tab & recent job-board->ATS redirects (session storage)     |  |
|  | - Attaches X-JobRecall-Token from chrome.storage.local                      |  |
|  | - Sole HTTP client communicating with http://127.0.0.1:8765                 |  |
|  +-------------------------------------+---------------------------------------+  |
+----------------------------------------|------------------------------------------+
                                         | HTTP (127.0.0.1:8765, Token Auth)
                                         v
+-----------------------------------------------------------------------------------+
| LOCAL PYTHON BACKEND (FastAPI + SQLite + LangChain)                               |
|                                                                                   |
|  +-----------------------------------------------------------------------------+  |
|  | FastAPI Router (/api/v1/...) + Auth & Localhost Middleware                  |  |
|  +--------+-----------------------+-------------------------+------------------+  |
|           |                       |                         |                     |
|           v                       v                         v                     |
|  +-----------------+   +---------------------+   +-----------------------------+  |
|  | Normalizer &    |   | Job, Resume &       |   | Eligibility Analyzer        |  |
|  | Matcher Service |   | Event CRUD Service  |   | 1. Cleaned Hash Cache Check |  |
|  | - URL/ID parser |   | - Canonical Job     |   | 2. LangChain Structured LLM |  |
|  | - Tier 1/2/3    |   | - Linked Postings   |   |    (15s asyncio timeout)    |  |
|  |   deduplication |   | - Reusable Resumes  |   | 3. Verbatim Quote Verifier  |  |
|  | - Dismissed     |   |   (text + PDF ref)  |   | 4. Profile + Resume Text    |  |
|  |   pair memory   |   | - Status & Notes    |   |    Comparator & Recommender |  |
|  +--------+--------+   +----------+----------+   +--------------+--------------+  |
|           |                       |                             |                 |
|           +-----------------------+-----------------------------+                 |
|                                   |                                               |
|                                   v                                               |
|  +-----------------------------------------------------------------------------+  |
|  | SQLite Database (jobrecall.db in WAL mode via SQLAlchemy 2.0 + Alembic)     |  |
|  +-----------------------------------------------------------------------------+  |
+-----------------------------------------------------------------------------------+
```

---

## 2. Database Schema (SQLite + SQLAlchemy 2.0)

We use a consolidated 7-table relational schema designed for fast local queries, multi-posting linkage per job, local resume management, reversible duplicate management, and content-addressed AI caching.

### 2.1 `jobs` (Canonical Job & Application State)
Represents one distinct job opening and the user's application relationship to it.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `TEXT (UUID)` | `PRIMARY KEY` | Internal canonical job ID. |
| `company_raw` | `TEXT` | `NOT NULL` | Display company name (e.g., `"Acme Corp, Inc."`). |
| `company_norm` | `TEXT` | `NOT NULL, INDEX` | Normalized company slug (e.g., `"acme"`). |
| `title_raw` | `TEXT` | `NOT NULL` | Display job title (e.g., `"Senior Backend Engineer (Python)"`). |
| `title_norm` | `TEXT` | `NOT NULL, INDEX` | Normalized title (e.g., `"senior backend engineer python"`). |
| `location_raw` | `TEXT` | `NULL` | Raw location string (e.g., `"Austin, TX (Hybrid)"`). |
| `location_norm` | `TEXT` | `NULL` | Normalized city/state/mode (e.g., `"austin tx"`). |
| `work_mode` | `TEXT` | `NULL` | `remote`, `hybrid`, `onsite`, or `unknown`. |
| `employer_req_id` | `TEXT` | `NULL, INDEX` | Verified employer/ATS requisition ID if known (e.g., `"JR-10492"`). |
| `canonical_ats_url` | `TEXT` | `NULL, INDEX` | Normalized ATS URL if known (e.g., `"https://boards.greenhouse.io/acme/jobs/49102"`). |
| `status` | `TEXT` | `NOT NULL, INDEX` | `saved`, `in_progress`, `applied`, `interviewing`, `offer`, `rejected`, `withdrawn`, `unknown`. |
| `applied_at` | `DATETIME` | `NULL` | Timestamp when user confirmed application submission. |
| `applied_via_posting_id` | `TEXT (UUID)` | `NULL, FK(postings.id)` | Which specific posting was used to apply. |
| `resume_id` | `TEXT (UUID)` | `NULL, FK(resumes.id) ON DELETE SET NULL` | Reference to which reusable `Resume` in `resumes` was submitted for this job. |
| `confirmation_ref` | `TEXT` | `NULL` | Optional confirmation ID / email snippet. |
| `created_at` | `DATETIME` | `NOT NULL` | First observed/saved timestamp (UTC). |
| `updated_at` | `DATETIME` | `NOT NULL` | Last status or note update timestamp (UTC). |

### 2.2 `postings` (Observed Job Board / ATS Pages)
A single `Job` can have multiple `Postings` (e.g., one on LinkedIn, one on Indeed, one on Workday).

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `TEXT (UUID)` | `PRIMARY KEY` | Posting ID. |
| `job_id` | `TEXT (UUID)` | `NOT NULL, FK(jobs.id) ON DELETE CASCADE, INDEX` | Linked canonical `Job`. |
| `platform` | `TEXT` | `NOT NULL, INDEX` | `linkedin`, `indeed`, `dice`, `greenhouse`, `workday`, `lever`, `ashby`, `icims`, `manual`, `other`. |
| `source_url_raw` | `TEXT` | `NOT NULL` | Original URL captured from browser. |
| `source_url_norm` | `TEXT` | `NOT NULL, INDEX` | Normalized URL (tracking params stripped). |
| `source_posting_id` | `TEXT` | `NULL, INDEX` | Platform-specific job ID (e.g., LinkedIn `currentJobId`, Indeed `jk`). |
| `requisition_id` | `TEXT` | `NULL, INDEX` | Extracted requisition ID on this page, if present. |
| `description_raw` | `TEXT` | `NULL` | Captured job description text. |
| `cleaned_text_hash` | `TEXT` | `NULL, INDEX` | SHA-256 of normalized description text (used for AI cache & text matching). |
| `first_seen_at` | `DATETIME` | `NOT NULL` | First time user captured this posting. |
| `last_seen_at` | `DATETIME` | `NOT NULL` | Most recent visit timestamp. |

*Unique index on `(platform, source_posting_id)` where `source_posting_id IS NOT NULL`.*

### 2.3 `job_events` (Immutable History & Notes Log)
Tracks every status transition, user note, and duplicate link/unlink action.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `TEXT (UUID)` | `PRIMARY KEY` | Event ID. |
| `job_id` | `TEXT (UUID)` | `NOT NULL, FK(jobs.id) ON DELETE CASCADE, INDEX` | Linked `Job`. |
| `event_type` | `TEXT` | `NOT NULL` | `created`, `status_changed`, `note_added`, `posting_linked`, `posting_unlinked`. |
| `old_status` | `TEXT` | `NULL` | Previous status (for `status_changed`). |
| `new_status` | `TEXT` | `NULL` | New status (for `status_changed`). |
| `note` | `TEXT` | `NULL` | User note or system context message. |
| `metadata_json` | `TEXT (JSON)` | `NULL` | Extra context (e.g., linked `posting_id`, confirmation ref). |
| `created_at` | `DATETIME` | `NOT NULL, INDEX` | Event timestamp (UTC). |

### 2.4 `dismissed_duplicates` (User-Rejected Duplicate Suggestions)
Prevents nagging the user about two postings they have already confirmed are separate openings.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `TEXT (UUID)` | `PRIMARY KEY` | Record ID. |
| `job_id` | `TEXT (UUID)` | `NOT NULL, FK(jobs.id) ON DELETE CASCADE` | Existing canonical `Job`. |
| `dismissed_url_norm` | `TEXT` | `NULL, INDEX` | Normalized URL of the non-matching page. |
| `dismissed_platform` | `TEXT` | `NULL` | Platform of the non-matching page. |
| `dismissed_posting_id` | `TEXT` | `NULL` | Platform posting ID or requisition ID that was rejected. |
| `created_at` | `DATETIME` | `NOT NULL` | When the user clicked "Not a duplicate". |

### 2.5 `requirement_analyses` (Cached LLM Extractions)
Keyed by `cleaned_text_hash` so identical job descriptions across visits (or across re-posts) never trigger duplicate LLM calls.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `TEXT (UUID)` | `PRIMARY KEY` | Analysis ID. |
| `cleaned_text_hash` | `TEXT` | `NOT NULL, INDEX` | SHA-256 of cleaned job description. |
| `schema_version` | `TEXT` | `NOT NULL` | Extraction schema version (e.g., `"v1.0"`). |
| `model_provider` | `TEXT` | `NOT NULL` | e.g., `"openai"`, `"anthropic"`, `"google"`, `"ollama"`. |
| `model_name` | `TEXT` | `NOT NULL` | e.g., `"gpt-4o-mini"`, `"claude-3-5-haiku"`. |
| `extracted_json` | `TEXT (JSON)` | `NOT NULL` | Validated `JobRequirementsExtraction` JSON payload. |
| `latency_ms` | `INTEGER` | `NOT NULL` | How long the LLM call took in milliseconds. |
| `created_at` | `DATETIME` | `NOT NULL` | Cache entry creation timestamp. |

### 2.6 `user_profile` (Single-Row Local Eligibility Profile)
Stores the user's optional local eligibility parameters for rule-based comparison.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `INTEGER` | `PRIMARY KEY CHECK (id = 1)` | Singleton row (`id = 1`). |
| `is_us_citizen` | `BOOLEAN` | `NULL` | `True`, `False`, or `None` (unspecified). |
| `is_permanent_resident` | `BOOLEAN` | `NULL` | Green card / permanent resident status. |
| `Authorized_to_work_us` | `BOOLEAN` | `NULL` | Currently authorized to work in the U.S. (including OPT/CPT/H1B/etc.). |
| `requires_visa_sponsorship` | `BOOLEAN` | `NULL` | Needs current or future employer visa sponsorship. |
| `clearance_level` | `TEXT` | `NULL` | `none`, `public_trust`, `secret`, `top_secret`, `ts_sci`, `ts_sci_poly`. |
| `highest_degree` | `TEXT` | `NULL` | `none`, `associate`, `bachelor`, `master`, `phd`. |
| `graduation_date` | `TEXT` | `NULL` | `YYYY-MM` format (e.g., `"2026-05"`). |
| `years_experience_total` | `REAL` | `NULL` | Total professional years of experience (e.g., `2.5`). |
| `preferred_work_modes` | `TEXT (JSON)` | `NULL` | e.g., `["remote", "hybrid"]`. |
| `current_state_code` | `TEXT` | `NULL` | e.g., `"TX"`, `"CA"`, `"NY"`. |
| `willing_to_relocate` | `BOOLEAN` | `NULL` | Whether user is open to relocation. |
| `skills_and_domains_json` | `TEXT (JSON)` | `NULL` | Aggregated list of skills/domains from resumes + user edits. |
| `updated_at` | `DATETIME` | `NOT NULL` | Last profile update timestamp. |

### 2.7 `resumes` (Reusable Resume Text + PDF Reference)
Because a user typically reuses the same 1–4 resumes across most applications, each variant is stored once. SQLite keeps the plain text and original filename (`pdf_ref`). The PDF bytes are copied once into `data/resumes/{resume_id}.pdf` (the same persisted data directory as the database). Every `Job` references `resume_id`. Download the stored copy with `GET /api/v1/resumes/{resume_id}/pdf`.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `TEXT (UUID)` | `PRIMARY KEY` | Resume ID. |
| `label` | `TEXT` | `NOT NULL` | User-friendly name (e.g., `"Backend Python v3"`, `"Full-Stack TS v2"`). |
| `target_role` | `TEXT` | `NULL` | Optional target role tag (e.g., `"Backend Engineer"`, `"ML Engineer"`). |
| `pdf_ref` | `TEXT` | `NULL` | Original PDF filename. The file bytes live at `data/resumes/{id}.pdf`, not at this path. |
| `content_text` | `TEXT` | `NOT NULL` | Full plain text of the resume (pasted directly or extracted once from the PDF via `pypdf`). |
| `content_hash` | `TEXT` | `NOT NULL, INDEX` | SHA-256 of normalized `content_text` (detects if two resume entries have identical text). |
| `parsed_json` | `TEXT (JSON)` | `NULL` | Structured data extracted from `content_text`: `skills`, `domains`, `degrees`, `graduation_date`, `years_experience_est`, `certifications`. |
| `is_default` | `BOOLEAN` | `NOT NULL DEFAULT 0` | Whether this is the default resume pre-selected when marking a job as applied. |
| `is_active` | `BOOLEAN` | `NOT NULL DEFAULT 1` | Soft-archive old resume versions without breaking historical `jobs.resume_id` references. |
| `created_at` | `DATETIME` | `NOT NULL` | Creation timestamp (UTC). |
| `updated_at` | `DATETIME` | `NOT NULL` | Last update timestamp (UTC). |

---

## 3. Normalization & Duplicate-Matching Algorithm

### 3.1 URL & Platform ID Extraction Rules (`services/normalizer.py`)
Before querying SQLite, the normalizer extracts canonical identifiers from any URL:

1. **LinkedIn:**
   - Match `/jobs/view/(\d+)` OR query parameter `currentJobId=(\d+)`.
   - Set `platform = "linkedin"`, `source_posting_id = <digits>`, `source_url_norm = "https://www.linkedin.com/jobs/view/<digits>"`.
2. **Indeed:**
   - Match query parameter `jk=([a-f0-9]+)` OR `vjk=([a-f0-9]+)`.
   - Set `platform = "indeed"`, `source_posting_id = <hex>`, `source_url_norm = "https://www.indeed.com/viewjob?jk=<hex>"`.
3. **Greenhouse:**
   - Match `boards.greenhouse.io/<company>/jobs/(\d+)` OR `job-boards.greenhouse.io/<company>/jobs/(\d+)` OR query parameter `gh_jid=(\d+)`.
   - Set `platform = "greenhouse"`, `source_posting_id = "<company>:<digits>"`, `requisition_id = <digits>`.
4. **Workday (`*.myworkdayjobs.com`):**
   - Match `/job/[^/]+/([^/?#]+)$` (often ends with `_JR12345` or `-R12345`).
   - Extract requisition ID suffix (`JR-\d+`, `R-\d+`, `REQ-\d+`) when present.
5. **Lever (`jobs.lever.co/<company>/<uuid>`):**
   - Set `platform = "lever"`, `source_posting_id = "<company>:<uuid>"`.
6. **Ashby (`jobs.ashbyhq.com/<company>/<uuid>`):**
   - Set `platform = "ashby"`, `source_posting_id = "<company>:<uuid>"`.
7. **Generic Fallback:**
   - Lowercase scheme + host, strip `utm_*`, `ref`, `trk`, `gh_src`, `source`, `fbclid`, `gclid` query parameters, strip trailing slash.

### 3.2 Company, Title, and Description Normalization
- **Company (`company_norm`):** Lowercase, strip corporate suffixes (`inc`, `llc`, `corp`, `corporation`, `ltd`, `co`, `group`, `technologies`, `labs`), strip punctuation, collapse whitespace.
- **Title (`title_norm`):** Lowercase, normalize common Roman numerals/levels (`iii` -> `3`, `ii` -> `2`, `sr.` -> `senior`, `jr.` -> `junior`, `swe` -> `software engineer`), strip punctuation.
- **Description (`cleaned_text_hash`):**
  1. Remove known job-board chrome lines (e.g., `"Reposted X days ago"`, `"Over 100 applicants"`, `"Actively reviewing applicants"`, `"Show more"`, `"Show less"`).
  2. Lowercase, collapse all whitespace sequences to a single space, trim.
  3. Compute `hashlib.sha256(cleaned_text.encode("utf-8")).hexdigest()`.

### 3.3 Four-Tier Matching Strategy (`services/matcher.py`)

When `POST /api/v1/jobs/resolve` is called:

| Tier | Match Condition | Match Label Returned | Auto-Merged? |
|---|---|---|---|
| **Tier 1: Exact Identity** | Same `(platform, source_posting_id)`, OR same `source_url_norm`, OR same `(company_norm, employer_req_id)` where `employer_req_id` is non-null. | `EXACT_MATCH` (`previously_applied` or `saved_in_progress`) | **Yes** — updates `last_seen_at` on the matched `Posting`. |
| **Tier 2: Strong Cross-Platform Match** | Same `canonical_ats_url`, OR same `(company_norm, title_norm)` AND identical `cleaned_text_hash` AND no conflicting `requisition_id`. | `STRONG_CANDIDATE` (`possible_duplicate` with `confidence >= 0.90`) | **No** — highlighted at top of Side Panel with 1-click **"Confirm Same Job"**. |
| **Tier 3: Fuzzy Possible Duplicate** | Same `company_norm` AND (exact `title_norm` OR title token Jaccard `>= 0.75` OR description trigram/token Jaccard `>= 0.65`) AND **not** conflicting `requisition_id` AND **not** in `dismissed_duplicates`. | `POSSIBLE_DUPLICATE` (`confidence 0.55 - 0.89`) | **No** — shown as reviewable card with match explanation & **"Link"** / **"Not Same Job"** buttons. |
| **Tier 4: Unmatched** | No candidate passes Tier 1–3 (or candidates have conflicting non-null requisition IDs like `JR-101` vs `JR-102`). | `NO_MATCH` (`no_local_record`) | **No** — shows clean state ready to Save or Mark Applied. |

> **Critical Guardrail:** If the incoming page has `requisition_id = "JR-1002"` and an existing job at the same company has `employer_req_id = "JR-1001"`, Tier 2 and Tier 3 **must not** suggest merging them (or must explicitly warn: *"Different requisition IDs detected: JR-1002 vs JR-1001"*).

---

## 4. LangChain Structured Extraction & Quote Verification Schema

### 4.1 Pydantic Extraction Schema (`schemas/analysis.py`)

```python
from typing import Literal
from pydantic import BaseModel, Field

RequirementLevel = Literal["required", "preferred", "unclear", "not_stated"]
ClearanceLevel = Literal["none", "public_trust", "secret", "top_secret", "ts_sci", "ts_sci_poly", "unspecified"]
DegreeLevel = Literal["none", "high_school", "associate", "bachelor", "master", "phd", "unspecified"]
WorkMode = Literal["remote", "hybrid", "onsite", "not_stated"]

class EvidenceSlot(BaseModel):
    status: RequirementLevel = Field(
        description="Whether this restriction is required, preferred, unclear, or not_stated in the text."
    )
    summary: str | None = Field(
        default=None,
        description="Concise 1-sentence human summary of the stated rule. Null if not_stated."
    )
    evidence_quote: str | None = Field(
        default=None,
        description="Exact verbatim substring copied character-for-character from the job description. Null if not_stated."
    )
    quote_verified: bool = Field(
        default=False,
        description="Populated by Python post-validator after checking substring presence in source text."
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
    category_label: str = Field(description="e.g., License, Certification, Language, Travel, On-Call, Employment Type")
    status: Literal["required", "preferred", "unclear"]
    summary: str
    evidence_quote: str
    quote_verified: bool = False

class JobRequirementsExtraction(BaseModel):
    work_authorization: WorkAuthSlot
    sponsorship: SponsorshipSlot
    security_clearance: ClearanceSlot
    education: EducationSlot
    graduation_window: GraduationWindowSlot
    experience: ExperienceSlot
    location_work_mode: LocationSlot
    other_restrictions: list[OtherRestrictionItem] = Field(default_factory=list)
    contradictions_or_ambiguities: list[str] = Field(
        default_factory=list,
        description="Note any internal contradictions in the posting (e.g., 'Title says Remote but body says 3 days/week in NYC')."
    )
```

### 4.2 Deterministic Quote Verification Function (`services/analyzer.py`)
Every non-null `evidence_quote` returned by the LLM is validated before saving or returning to the extension:

```python
import re

def verify_quote_in_text(quote: str | None, source_text: str) -> bool:
    if not quote or not quote.strip():
        return False
    norm_quote = re.sub(r"\s+", " ", quote.strip().lower())
    norm_source = re.sub(r"\s+", " ", source_text.strip().lower())
    if norm_quote in norm_source:
        return True
    # Fallback: strip punctuation for minor bullet/quote mark differences
    alnum_quote = re.sub(r"[^a-z0-9\s]", "", norm_quote)
    alnum_source = re.sub(r"[^a-z0-9\s]", "", norm_source)
    return len(alnum_quote) >= 10 and alnum_quote in alnum_source
```
If `verify_quote_in_text` returns `False`, the backend sets `slot.quote_verified = False` and overrides any automatic profile evaluation for that slot to `needs_review`.

---

## 5. REST API Contract (`/api/v1`)

All endpoints bind to `127.0.0.1:8765` and require the header `X-JobRecall-Token: <local_secret>` (except `GET /api/v1/health` which returns `{ "status": "ok", "auth_valid": bool, "ai_configured": bool }`).

| Method & Route | Request Body / Query | Response Summary |
|---|---|---|
| `GET /api/v1/health` | Header `X-JobRecall-Token` (optional) | Service status, DB connectivity, token validity, configured LLM provider/model. |
| `POST /api/v1/jobs/resolve` | `ResolveJobRequest` (URL, platform, extracted IDs, company, title, location, description text) | `ResolveJobResponse`: `match_state` (`previously_applied`, `saved_in_progress`, `possible_duplicate`, `no_local_record`), matched `JobDetail` (if Tier 1), `candidate_matches: list[CandidateMatch]` (if Tier 2/3), and `cached_analysis` if already cached for this `cleaned_text_hash`! |
| `POST /api/v1/jobs` | `CreateJobRequest` (posting payload + initial `status`, optional `resume_version`, `note`) | Creates `Job`, initial `Posting`, and `created` `JobEvent`. Returns `JobDetail`. |
| `GET /api/v1/jobs` | Query: `status`, `q` (search company/title), `limit`, `offset` | Paginated list of tracked jobs for history search/export. |
| `GET /api/v1/jobs/{job_id}` | — | Full `JobDetail` including all linked `Postings` and chronological `JobEvents`. |
| `PATCH /api/v1/jobs/{job_id}/status` | `UpdateJobStatusRequest` (`status`, `applied_at`, `resume_version`, `confirmation_ref`, `note`) | Updates `Job.status` and appends a `status_changed` `JobEvent`. |
| `POST /api/v1/jobs/{job_id}/notes` | `AddNoteRequest` (`note`) | Appends a `note_added` `JobEvent`. |
| `POST /api/v1/jobs/{job_id}/link-posting` | `LinkPostingRequest` (`posting_payload`) | Links a new URL/platform `Posting` to an existing `Job` and logs `posting_linked`. |
| `POST /api/v1/jobs/{job_id}/unlink-posting/{posting_id}` | — | Splits a linked `Posting` out into its own separate `Job` record (reversible merge). |
| `POST /api/v1/jobs/{job_id}/dismiss-duplicate` | `DismissDuplicateRequest` (`url_norm`, `platform`, `posting_id`) | Records a `dismissed_duplicates` row so Tier 2/3 won't suggest this pair again. |
| `POST /api/v1/jobs/analyze` | `AnalyzeJobRequest` (`description_text`, `resume_id: str | None = None`, `force_refresh: bool = False`) | Checks cache or runs LangChain extraction (15s timeout) + quote verification + profile & resume comparison (including best resume recommendation & missing skill check). |
| `GET /api/v1/resumes` | — | Lists all stored reusable `Resume` entries (`id`, `label`, `target_role`, `pdf_ref`, `is_default`, `parsed_json`). |
| `POST /api/v1/resumes` | JSON (`label`, `target_role`, `content_text`, `is_default`, `auto_sync_profile`) | Saves pasted resume text in SQLite and optionally syncs `UserProfile`. |
| `POST /api/v1/resumes/extract-pdf` | `multipart/form-data` (`file`, `label`, optional `content_text`) | Copies the PDF into `data/resumes/{id}.pdf`, extracts text, and saves the resume row. |
| `GET /api/v1/resumes/{resume_id}/pdf` | — | Downloads the PDF copy stored by the service. |
| `PATCH /api/v1/resumes/{resume_id}` | `UpdateResumeRequest` (`label`, `target_role`, `pdf_ref`, `content_text`, `is_default`, `is_active`) | Updates resume metadata, text, or active status. |
| `DELETE /api/v1/resumes/{resume_id}` | — | Archives (`is_active = False`) or deletes an unused resume record. |
| `GET /api/v1/profile` | — | Returns current `UserProfile`. |
| `PUT /api/v1/profile` | `UserProfileUpdate` | Updates `UserProfile` and re-evaluates comparisons dynamically. |
| `GET /api/v1/export` | — | Exports all jobs, postings, events, and profile as a portable JSON bundle. |

> **Performance optimization in `/api/v1/jobs/resolve`:** Because `resolve` already computes `cleaned_text_hash`, it automatically checks the `requirement_analyses` table in the same SQLite transaction. If this job description was already analyzed in the past, the cached AI analysis is returned **instantly alongside the history lookup** with zero extra round-trips!

---

## 6. Docker Architecture & Local Security Model

Running the FastAPI backend in Docker with `restart: unless-stopped` solves the biggest usability friction of local browser-extension backends: **the backend stays running automatically in the background without keeping a terminal window open.**

### 6.1 Docker Compose & SQLite WAL Volume Rules
1. **Strict Host Loopback Port Binding (`127.0.0.1:8765:8765`):**
   - Inside the container, Uvicorn listens on `0.0.0.0:8765` so Docker bridge networking can forward traffic.
   - In `docker-compose.yml`, the port mapping **must** be `"127.0.0.1:8765:8765"` (never `"8765:8765"`, which exposes the port on `0.0.0.0` to the local Wi-Fi/LAN).
2. **Directory Mount for SQLite WAL Mode (`./backend/data:/app/data`):**
   - SQLite in WAL (`Write-Ahead Logging`) mode creates sidecar files (`jobrecall.db-wal` and `jobrecall.db-shm`) in the same directory as `jobrecall.db`.
   - Therefore, we mount the **directory** `./backend/data:/app/data` (with `JOBRECALL_DB_URL=sqlite:////app/data/jobrecall.db`) rather than mounting a single file.
3. **Automatic Migrations on Startup (`entrypoint.sh`):**
   - When the container starts, `entrypoint.sh` runs `alembic upgrade head` before starting `uvicorn`, ensuring the SQLite schema is always up to date.
4. **Host LLM Connectivity (`host.docker.internal`):**
   - If the user configures local Ollama on their host machine instead of a cloud LLM API, `extra_hosts: ["host.docker.internal:host-gateway"]` allows the container to reach `http://host.docker.internal:11434`.

```yaml
# Reference docker-compose.yml
services:
  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: jobrecall-backend
    restart: unless-stopped
    ports:
      - "127.0.0.1:8765:8765"
    env_file:
      - ./backend/.env
    environment:
      - JOBRECALL_HOST=0.0.0.0
      - JOBRECALL_PORT=8765
      - JOBRECALL_DB_URL=sqlite:////app/data/jobrecall.db
    volumes:
      - ./backend/data:/app/data
    extra_hosts:
      - "host.docker.internal:host-gateway"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/v1/health')"]
      interval: 30s
      timeout: 5s
      retries: 3
```

### 6.2 Authentication & Key Isolation
1. **Shared Local API Token (`X-JobRecall-Token`):**
   - Set in `backend/.env` (or auto-generated on first startup and logged to `docker compose logs backend`).
   - Pasted once into the Extension Side Panel Settings (`chrome.storage.local`).
   - Prevents any malicious webpage from forging requests to `127.0.0.1:8765`, alongside strict CORS (`chrome-extension://<extension-id>`).
2. **LLM Key Isolation:** `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` live exclusively in `backend/.env` (gitignored) and are injected into the container via `env_file`. They are never exposed in extension code.
