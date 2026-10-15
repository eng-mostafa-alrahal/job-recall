# JobRecall — Product & Technical Specification (v1.0)

> **Tagline:** Remember where you applied. Understand whether a job fits before applying again.
>
> **Status:** Refined Specification (v1.0)  
> **Type:** Personal-use, local-first Chrome Extension (Manifest V3) + Python FastAPI + SQLite + LangChain service

---

## 1. Overview & Target of the Project

**JobRecall** is a personal decision-support and application-memory tool for job seekers. When a user views or captures a job posting, the browser extension immediately checks the user's local SQLite history and displays whether they have already applied, saved the role, or encountered a likely duplicate across another job board or company career site.

Independently, an on-demand (or opt-in automatic) **LangChain AI extraction pipeline** analyzes the job description to surface explicit eligibility restrictions—such as U.S. citizenship, visa sponsorship, security clearance, education level, graduation window, minimum years of experience, and location/work mode—backed by verbatim source quotes and compared against an optional local user profile.

### Core Goals
1. **Instant Application Recall (<1s):** Show known history for the current job without manual searching, completely independent of LLM speed or availability.
2. **Accurate Cross-Platform Deduplication:** Identify the same job across LinkedIn, Indeed, Dice, and ATS platforms (Workday, Greenhouse, Lever, Ashby) without falsely merging distinct openings at the same company.
3. **Verifiable Requirement Extraction:** Surface stated restrictions with required vs. preferred distinctions and verified verbatim quotes from the posting text.
4. **100% Local Data Ownership:** Keep all application records and personal profile data in a local SQLite database on the user's machine (`127.0.0.1`).
5. **Graceful Degradation:** Enforce a strict 15-second cutoff on AI calls with caching and clear fallback states.

---

## 2. Key Architectural Refinements Over v0.1

To make JobRecall reliable in real-world daily job hunting, this specification incorporates seven critical refinements over the initial draft:

### 2.1 Chrome Side Panel + SPA Split-View Awareness
- **Problem:** On LinkedIn (`/jobs/search/?currentJobId=...`) and Indeed (`?vjk=...`), users browse jobs in a split-screen master-detail view. Clicking a new job card mutates query parameters and DOM nodes via Single-Page Application (SPA) navigation without reloading the page, and clicking the page closes a standard Chrome Popup.
- **Solution:**
  - Use the **Chrome Side Panel API (`chrome.sidePanel`)** as the primary UI so JobRecall stays open alongside the job board as the user clicks from job to job.
  - Detect SPA job switches via `chrome.tabs.onUpdated` (URL/query parameter changes) and DOM mutation observers on the active job detail pane.
  - Extract platform job IDs from both path segments (`/jobs/view/12345`) and query parameters (`currentJobId=12345`, `vjk=abcde`, `gh_jid=98765`).

### 2.2 MV3 Background Service Worker as the Sole API Bridge
- **Problem:** Modern browsers enforce Private Network Access (PNA) and Content Security Policy (CSP) restrictions that block Content Scripts running inside `https://www.linkedin.com` from calling `http://127.0.0.1`.
- **Solution:** Content Scripts **never** call FastAPI directly. Content Scripts only extract DOM data and send messages via `chrome.runtime.sendMessage` to the **Extension Background Service Worker** (or Side Panel), which owns the local API token and performs all `fetch("http://127.0.0.1:8765/...")` requests.

### 2.3 Two-Step "Apply Redirect" Bridging (Job Board -> External ATS)
- **Problem:** When viewing a job on LinkedIn or Indeed, the destination ATS URL (e.g., `acme.wd5.myworkdayjobs.com/.../job/JR-10492`) is often hidden behind an obfuscated redirect until the user clicks "Apply".
- **Solution:**
  - The Background Service Worker keeps an ephemeral `lastViewedBoardPosting` state in `chrome.storage.session` (valid for 5 minutes).
  - When a user clicks "Apply" on LinkedIn/Indeed and lands on an external ATS page in a new tab with a matching company/title, JobRecall offers a one-click banner: **"Link this ATS page to the LinkedIn posting for [Title] at [Company]?"** (or auto-links if the user clicked "Track Apply Redirect" from the Side Panel).
  - This connects the job board posting (`LinkedIn ID`) and the canonical ATS posting (`Workday JR-10492`) under a single `Job` record.

### 2.4 Consolidated Data Model (`Job` + `Posting` + `JobEvent`)
- **Problem:** Separating `Job` and `Application` into distinct tables adds unnecessary join complexity for a single-user tracker where a user's relationship to a canonical job opening is 1-to-1.
- **Solution:**
  - **`Job`** represents the canonical job opening *and* the user's current tracking state (`status`, `applied_at`, `applied_via_posting_id`, `resume_version`, `is_archived`).
  - **`Posting`** represents each URL/platform where that `Job` was observed (1 `Job` -> many `Postings`).
  - **`JobEvent`** records an immutable audit trail of every status transition, note, duplicate link/unlink action, and confirmation reference.

### 2.5 Cleaned Text Normalization & Two-Tier Hashing
- **Problem:** Job boards inject dynamic text ("Posted 2 days ago", "Over 100 applicants", "Meet the hiring team") that changes a raw SHA-256 hash every day even when the job description itself is identical.
- **Solution:**
  - Strip dynamic UI chrome, normalize whitespace/case/punctuation, and compute `cleaned_text_hash` (SHA-256 of normalized description text) for **LLM cache lookups**.
  - For **cross-platform duplicate detection**, compute token sets / trigram similarity on the cleaned description alongside normalized `(company, title, location)` so slight formatting differences between LinkedIn and Greenhouse still match reliably.

### 2.6 Fixed-Slot Pydantic Extraction + Deterministic Quote Verification
- **Problem:** If an LLM returns a variable-length array of requirements, it is ambiguous whether an omitted category was "not stated" or forgotten by the model. Furthermore, LLMs occasionally paraphrase quotes.
- **Solution:**
  - Use a **fixed-slot Pydantic schema** with explicit fields for all 7 core categories (`work_authorization`, `sponsorship`, `security_clearance`, `education`, `graduation_window`, `experience`, `location_work_mode`) plus `other_restrictions: list[ExtractedItem]`.
  - Run a **deterministic Python post-validator** after LangChain returns: verify that every `evidence_quote` appears in the captured job text (using whitespace/punctuation-insensitive substring matching). Any unverified quote is flagged `quote_verified = False` and downgraded to `needs_review`.

### 2.7 Hard-Gate vs. Resume-Aware Profile Comparison (Text + Reference Design)
- **Problem:** Relying only on a manually typed `UserProfile` form is tedious and misses what is actually written on the user's resume(s). At the same time, copying binary PDF files for every application is wasteful because a job seeker typically reuses the same 1–4 resume variants across dozens of applications.
- **Solution:**
  - **Lightweight Reusable Resume Table (`resumes`):** Store each reusable resume variant once. SQLite keeps the plain text (`content_text`) and parsed skills (`parsed_json`). The original PDF is copied once into the service data directory (`data/resumes/{resume_id}.pdf`) so the copy remains available if the user deletes or moves the source file. Jobs still link only `resume_id`.
  - **Reference by `resume_id` on Every Application:** When marking a job as `applied`, the `Job` record simply stores `resume_id` (a foreign key to the reusable resume in `resumes`).
  - **Best Resume Recommendation & Missing-Skill Warning:** When comparing extracted job requirements against the user's profile and stored resume text:
    - **Hard Gates (Deterministic comparison):** Citizenship, Visa Sponsorship, Security Clearance, Minimum Degree, Graduation Window, Total Years of Experience, and Work Mode preference $\rightarrow$ evaluated into `likely_meets` (Green), `possible_mismatch` (Red), `needs_review` (Yellow), or `profile_missing` (Gray).
    - **Resume Fit & Skill Coverage:** Compares required domains/skills/certifications against the stored `content_text` / `parsed_json` of the user's resumes, recommends **which stored resume variant fits best**, and warns if a stated requirement is **missing from the selected resume**.

---

## 3. End-to-End User Experience

### Flow A: Opening or Capturing a Job Posting (History First)

1. **Detection or Manual Trigger:**
   - As the user browses a supported site (LinkedIn, Indeed, Dice, Greenhouse, Lever, Workday, Ashby) with the Side Panel open, the Content Script extracts available metadata (`platform`, `source_url`, `source_posting_id`, `requisition_id`, `company`, `title`, `location`, `description_text`).
   - On unsupported sites—or if DOM selectors change—the user can highlight text and click **"Capture Selected Text"** or paste into the Side Panel's **Manual Capture** form.
2. **Instant Resolution (`POST /api/jobs/resolve`):**
   - The extension sends the extracted payload to the local FastAPI backend.
   - Within **<1 second**, the Side Panel renders one of four match states:
     - **Previously Applied (High Visibility Banner):** Shows application date, platform used, current status (`applied`, `interviewing`, `rejected`, `offer`), resume version used, latest note, and full event timeline.
     - **Saved / In Progress:** Shows that the job was previously bookmarked or started, with one-click **"Confirm Applied"** action.
     - **Possible Duplicate Found:** Displays candidate match(es) with match reason (e.g., *"Same company & title, 88% description match on Greenhouse"*). User can click **"Same Job — Link Posting"** or **"Different Job — Keep Separate"**.
     - **No Local Record Found:** Clearly states no local match exists and offers **"Save Job"** or **"Mark as Applied"**.
3. **Quick User Actions:**
   - **Save Job** (`status = saved`)
   - **Mark as Applied** (`status = applied`, records timestamp, platform, optional resume version & confirmation note)
   - **Change Status** (`saved`, `in_progress`, `applied`, `interviewing`, `offer`, `rejected`, `withdrawn`, `unknown`)
   - **Add Note** (appends to `JobEvent` log)
   - **Link / Unlink Posting** (reversibly merges or separates a posting from a `Job`)

### Flow B: Analyzing Eligibility Requirements (AI Second)

1. **Trigger & Consent:**
   - User clicks **"Analyze Requirements"** (or enables *"Auto-analyze when viewing a job"* after accepting the one-time external LLM privacy notice).
2. **Cache Lookup:**
   - Backend computes `cleaned_text_hash` and checks `RequirementAnalysis` for a cached result matching `(cleaned_text_hash, schema_version, model_provider, model_name)`.
   - If found, returns immediately (<100ms) with `cached: true`.
3. **LangChain Structured Extraction (if uncached):**
   - Backend invokes the configured LLM via LangChain `.with_structured_output(JobRequirementsExtraction)` with a strict **15-second timeout**.
   - Deterministic post-processor validates all `evidence_quote` fields against the source text.
4. **Local Profile & Resume Evaluation:**
   - If the user has uploaded resume(s) or configured their local `UserProfile`, the backend runs the `ProfileAndResumeComparator`:
     - Attaches a match label (`likely_meets`, `possible_mismatch`, `needs_review`, `profile_missing`, or `not_applicable`) to each category.
     - Evaluates all uploaded resumes against the job's required domains/skills to recommend the **Best Matching Resume** (e.g., *"Recommended Resume: Backend_Python_v3.pdf (matches 6/7 required skills)"*) and flags any required skills missing from that resume.
5. **UI Presentation:**
   - Each restriction category is rendered as a scannable card showing:
     - Category name & status badge (`Required` / `Preferred` / `Unclear` / `Not Stated`)
     - Profile comparison pill (`Likely Meets` / `Possible Mismatch` / `Needs Review`)
     - Short normalized summary
     - Collapsible/highlighted **verbatim quote** from the job description (with warning badge if quote verification failed)
   - Categories with `not_stated` are collapsed into a compact footer (*"Not mentioned in posting: Clearance, Graduation Window"*).

---

## 4. Requirement Categories & Interpretation Rules

| Category | Key Attributes Extracted | Strict Interpretation Rule |
|---|---|---|
| **1. Work Authorization / Citizenship** | `requires_us_citizenship`, `requires_permanent_residency`, `general_work_auth_stated` | Never equate general work authorization ("authorized to work in the US") with U.S. citizenship. |
| **2. Visa Sponsorship** | `sponsorship_available` (`yes` / `no` / `case_by_case` / `not_stated`) | Track separately from work authorization (e.g., OPT/CPT candidates may have current work auth but require future sponsorship). |
| **3. Security Clearance** | `clearance_level` (`none`, `public_trust`, `secret`, `top_secret`, `ts_sci`, `polygraph`), `clearance_timing` (`active_required` vs `eligible_to_obtain`) | Distinguish between **must currently hold an active clearance** vs. **ability/eligibility to obtain**. |
| **4. Education** | `minimum_degree` (`none`, `associate`, `bachelor`, `master`, `phd`), `preferred_degree`, `fields_of_study`, `accepts_equivalent_experience` | Preserve minimum vs. preferred degree and explicitly note whether equivalent practical experience is accepted in lieu of a degree. |
| **5. Graduation Window** | `earliest_grad_date`, `latest_grad_date`, `window_description` | Capture stated new-grad/internship windows (e.g., *"Must graduate between Dec 2026 and June 2027"*). |
| **6. Experience** | `min_years_total`, `preferred_years_total`, `domain_specific_years` | Distinguish required minimum years from preferred years; separate general software/industry experience from niche tool experience. |
| **7. Location & Work Mode** | `work_mode` (`remote`, `hybrid`, `onsite`, `not_stated`), `allowed_locations`, `relocation_assistance` | Never assume a job is remote unless explicitly stated; capture state/country residency restrictions (e.g., *"Remote within CA, NY, WA"*). |
| **8. Other Restrictions** | List of licenses, certifications, language fluency, travel %, shift/on-call, or employment type (`contract`, `intern`, `full_time`) | Extract only when explicitly stated in the text. |

---

## 5. Error States & Performance Targets

- **Local History Latency Target:** `< 200ms` backend processing; `< 1s` end-to-end in the Side Panel.
- **AI Extraction Latency Target:** `< 8s` typical; hard cutoff at **`15.0s`** (`asyncio.wait_for` on backend + `AbortController` in extension).
- **Distinct Error States (Never Fail Silently):**
  1. `BACKEND_OFFLINE`: Local FastAPI service is not reachable at `127.0.0.1:8765` — show startup instructions & retry button.
  2. `AUTH_MISMATCH`: Local API token (`X-JobRecall-Token`) missing or invalid.
  3. `INSUFFICIENT_TEXT`: Captured job description is `< 100` characters (e.g., user hasn't clicked a job card or page didn't load) — prompt user to paste or select text manually.
  4. `AI_NOT_CONFIGURED`: No LLM API key or local Ollama endpoint configured — history works normally; AI panel shows setup instructions.
  5. `AI_TIMEOUT`: LLM took longer than 15 seconds — show *"Analysis timed out after 15s"* with **Retry** button.
  6. `AI_PROVIDER_ERROR`: Rate limit (`429`), invalid API key (`401`), or malformed structured output — display specific provider reason.

---

## 6. MVP Acceptance Checklist

- [ ] **History Independence:** Revisiting a saved or applied job displays accurate history in `< 1s` even with no LLM API key configured or no internet connection.
- [ ] **Explicit Applied Status:** Clicking "Apply" on a job page never sets status to `applied` without explicit user confirmation.
- [ ] **Safe Deduplication:** Exact ID matches resolve automatically; fuzzy company + title + location/description matches surface as **Possible Duplicate** with reversible Link/Dismiss actions.
- [ ] **Distinct Requisitions Protected:** Two openings at the same company with the same title but different requisition IDs (e.g., `JR-1001` vs `JR-1002`) are never silently merged.
- [ ] **Complete Categorization:** AI extraction distinguishes `required`, `preferred`, `unclear`, and `not_stated` across all 7 core categories.
- [ ] **Verified Evidence:** Every extracted restriction includes a verbatim `evidence_quote` checked against the captured job description text.
- [ ] **Content-Hash Caching:** Re-opening an already-analyzed job returns the cached analysis immediately without making a second LLM call.
- [ ] **15-Second Cutoff:** Slow LLM calls abort cleanly at 15 seconds with a retry UI.
- [ ] **Dockerized Always-On Backend & Local Security:** Backend runs via `docker compose up -d` (`restart: unless-stopped`), binds host port strictly to `127.0.0.1:8765:8765`, persists SQLite in a mounted `./backend/data` volume, verifies `X-JobRecall-Token`, and stores LLM keys only in `backend/.env`.
