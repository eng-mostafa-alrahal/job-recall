# JobRecall — Step-by-Step Implementation Roadmap

This document provides the concrete execution blueprint for building **JobRecall** from an empty repository to a complete, tested local application tracker and AI eligibility analyzer.

> **Implementation Status:**
> - [x] **Phase 0:** Foundation, Docker Compose, SQLite WAL + Alembic, Pydantic v2 Schemas & Test Fixtures (`COMPLETED`)
> - [x] **Phase 1:** Core Local Application Tracker & Chrome MV3 Side Panel (`COMPLETED`)
> - [x] **Phase 2:** 4-Tier Duplicate Matcher, SPA Site Adapters & 2-Step Apply Redirect Bridge (`COMPLETED`)
> - [x] **Phase 3:** LangChain Structured Requirement Extraction, Deterministic Quote Verifier & Content-Hash Cache (`COMPLETED`)
> - [x] **Phase 4:** Reusable Resumes (Text + PDF Ref), Auto-Profile Sync, Hard-Gate Comparator & JSON Export (`COMPLETED`)
> - [ ] **Phase 5:** Optional Future Enhancements (Email receipt import, multi-browser build)

---

## Phase 0 — Foundation, Schemas & Test Fixtures

**Goal:** Establish the monorepo structure (`backend/` and `extension/`), SQLite database + Alembic migrations, Pydantic schemas, configuration loader, and a realistic corpus of test job postings before writing UI or AI code.

### Step 0.1: Backend & Docker Scaffolding (`backend/` & `docker-compose.yml`)
- Create `backend/pyproject.toml` (or `requirements.txt`) with pinned dependencies:
  - Core: `fastapi`, `uvicorn[standard]`, `sqlalchemy>=2.0`, `alembic`, `pydantic>=2.0`, `pydantic-settings`, `python-dotenv`, `python-multipart`
  - Resume Parsing: `pypdf`, `python-docx`
  - AI (used in Phase 3): `langchain-core`, `langchain-openai`, `langchain-anthropic`, `langchain-google-genai`, `langchain-ollama`
  - Dev/Test: `pytest`, `pytest-asyncio`, `httpx`, `ruff`
- Create `backend/.env.example` containing:
  - `JOBRECALL_HOST=0.0.0.0` (inside Docker) / `127.0.0.1` (local dev)
  - `JOBRECALL_PORT=8765`
  - `JOBRECALL_API_TOKEN=change-me-or-auto-generate`
  - `JOBRECALL_DB_URL=sqlite:////app/data/jobrecall.db`
  - `LLM_PROVIDER=openai` (`openai` | `anthropic` | `google` | `ollama`)
  - `LLM_MODEL=gpt-4o-mini`
  - `OPENAI_API_KEY=`
- Create `backend/Dockerfile` (`python:3.11-slim`, non-root user, installs dependencies) and `backend/entrypoint.sh` (runs `alembic upgrade head` then `uvicorn app.main:app --host 0.0.0.0 --port 8765`).
- Create root `docker-compose.yml` with:
  - Port bound strictly to `"127.0.0.1:8765:8765"`.
  - Directory volume `./backend/data:/app/data` so `jobrecall.db` and its SQLite WAL/SHM files persist on the host.
  - `restart: unless-stopped` and container `healthcheck` hitting `/api/v1/health`.

### Step 0.2: Database & ORM Models
- Implement `backend/app/database.py` configuring SQLite with `PRAGMA journal_mode=WAL;` and `PRAGMA foreign_keys=ON;`.
- Implement SQLAlchemy 2.0 models in `backend/app/models/`:
  - `Job`, `Posting`, `JobEvent`, `DismissedDuplicate`, `RequirementAnalysis`, `UserProfile`, `Resume`.
- Initialize Alembic (`backend/migrations/`) and generate the initial migration.

### Step 0.3: Test Corpus of Representative Job Postings (`backend/tests/fixtures/sample_postings.json`)
Create 8–10 realistic test job descriptions covering all edge cases:
1. **Defense Contractor Role:** Requires U.S. Citizenship + Active Secret Clearance.
2. **Standard Tech Role (No Sponsorship):** "Must be authorized to work in the U.S. We do not offer visa sponsorship now or in the future."
3. **New Grad Role:** "Must graduate with a BS/MS in Computer Science between Dec 2026 and Aug 2027."
4. **Senior Role with Equivalent Experience:** "Bachelor's degree in CS or equivalent practical experience; 5+ years required, 8+ years preferred."
5. **Contradictory Location Role:** Header says "Remote - US", body says "Must reside within 50 miles of Austin, TX and work in-office 3 days/week."
6. **Same Job Across Two Platforms:** One LinkedIn URL and one Greenhouse URL for the exact same role at Acme Corp.
7. **Same Title, Different Requisition IDs:** Two "Software Engineer II" openings at Acme Corp (`JR-1001` in Seattle vs. `JR-1002` in New York).
8. **Minimal/Vague Posting:** Very short description with no citizenship, clearance, or degree mentioned (tests `not_stated` behavior).

### Step 0.4: Extension Scaffolding (`extension/`)
- Initialize Vite + TypeScript project in `extension/`.
- Configure `manifest.json` (Manifest V3):
  - Permissions: `sidePanel`, `storage`, `activeTab`, `tabs`, `scripting`.
  - Host permissions: `http://127.0.0.1:8765/*`.
  - Configure `background.service_worker` and `side_panel.default_path`.

**Phase 0 Done When:**
- `docker compose up --build -d` starts the backend cleanly on `http://127.0.0.1:8765/api/v1/health` with SQLite persisted in `./backend/data/jobrecall.db`.
- `pytest` runs cleanly against an in-memory SQLite database.
- `npm run build` in `extension/` outputs a loadable unpacked Chrome extension in `extension/dist/`.

---

## Phase 1 — Core Local Application Tracker (First Usable Product — No AI Needed)

**Goal:** Deliver a reliable, sub-second local job memory tool where a user can capture a job, save it, mark it as applied, add notes, and immediately see its status when revisiting the page.

### Step 1.1: Core FastAPI Endpoints
- Implement `GET /api/v1/health` and token authentication middleware (`X-JobRecall-Token`).
- Implement `POST /api/v1/jobs` (Save or Mark as Applied).
- Implement `GET /api/v1/jobs/{job_id}`, `PATCH /api/v1/jobs/{job_id}/status`, and `POST /api/v1/jobs/{job_id}/notes`.
- Implement basic Tier 1 exact matching in `POST /api/v1/jobs/resolve` (exact normalized URL or `(platform, source_posting_id)`).

### Step 1.2: Extension Side Panel & Manual Capture
- Build the Side Panel UI (`extension/src/sidepanel/`) with four clean sections:
  1. **Connection Bar:** Green/Red indicator for local backend (`127.0.0.1:8765`) + Settings drawer to configure API token.
  2. **Current Job Header & Match Banner:** Shows `Previously Applied` (Green/Blue), `Saved / In Progress` (Amber), `Possible Duplicate` (Purple), or `No Local Record Found` (Neutral).
  3. **Action Controls:** `Save Job`, `Confirm Applied` (opens compact modal for date, resume version, and optional confirmation note), `Status Dropdown`, and `Add Note`.
  4. **Manual Capture / Edit Drawer:** Pre-fills extracted `company`, `title`, `location`, `requisition_id`, and `description_text` from the active tab, allowing the user to edit or paste manually on any unsupported site.

### Step 1.3: Generic Page Extractor + Selection Capture
- Write a baseline Content Script (`extension/src/content/extractor.ts`) that extracts:
  - JSON-LD `JobPosting` schema (`<script type="application/ld+json">`) when present on career sites! *(Note: Many ATS pages embed structured `JobPosting` JSON-LD with exact `title`, `hiringOrganization.name`, `identifier.value`, and `description`—parsing JSON-LD first gives rock-solid extraction across hundreds of sites without brittle CSS selectors.)*
  - OpenGraph / `<title>` / user-highlighted text (`window.getSelection()`) as fallback.

**Phase 1 Done When:**
- You can open a job posting, save it or mark it as applied with a note and resume version, close the browser, reopen that URL later, and see your full application history in `< 1s`—even with the internet disconnected.

---

## Phase 2 — Smart Duplicate Detection, Site Adapters & Redirect Bridging

**Goal:** Catch cross-posted duplicates (e.g., LinkedIn -> Greenhouse/Workday) and handle SPA split-pane browsing smoothly without falsely merging separate job requisitions.

### Step 2.1: Normalization & Multi-Tier Matching Engine
- Implement `backend/app/services/normalizer.py`:
  - Platform-specific URL & query-param ID parsers (`currentJobId`, `vjk`, `jk`, `gh_jid`, Workday `_JR...`, Lever/Ashby UUIDs).
  - Company name, job title, and boilerplate-stripped description normalizers.
- Implement `backend/app/services/matcher.py`:
  - Tier 1 (Exact ID/URL), Tier 2 (Canonical ATS URL or Company + Title + Cleaned Text Hash), Tier 3 (Company + Fuzzy Title/Description Jaccard similarity).
  - Enforce the **Conflicting Requisition ID Guardrail** (`JR-1001 != JR-1002`).
  - Filter out pairs recorded in `dismissed_duplicates`.
- Implement `POST /api/v1/jobs/{job_id}/link-posting`, `POST /api/v1/jobs/{job_id}/unlink-posting/{posting_id}`, and `POST /api/v1/jobs/{job_id}/dismiss-duplicate`.

### Step 2.2: Site-Specific Adapters & SPA Navigation Observer
- Add dedicated DOM + URL adapters in `extension/src/content/adapters/` for:
  1. **LinkedIn Jobs** (handles split-view `/jobs/search/?currentJobId=...`, `/jobs/collections/...`, and `/jobs/view/...`).
  2. **Indeed** (handles split-view `?vjk=...` and `/viewjob?jk=...`).
  3. **Major ATS Portals:** Greenhouse, Workday (`myworkdayjobs.com`), Lever, Ashby.
- Add SPA URL listener in `background.ts` (`chrome.tabs.onUpdated`) + `MutationObserver` debounce in content script so clicking a new job card in LinkedIn/Indeed automatically re-runs `/api/v1/jobs/resolve` and updates the Side Panel.

### Step 2.3: "Recent Job Board -> ATS Tab" Bridge
- In `background.ts`, store the most recently viewed job board posting in `chrome.storage.session` (`lastBoardPosting`, 5-minute TTL).
- When a new tab opens on an ATS domain (`myworkdayjobs.com`, `greenhouse.io`, `lever.co`, `ashbyhq.com`) and matches the normalized company or title of `lastBoardPosting`, display a 1-click prompt in the Side Panel:
  - *"Did you just click Apply from LinkedIn for [Title] at [Company]? [Link Postings]"*

**Phase 2 Done When:**
- Viewing a job on LinkedIn and then opening the same job on Greenhouse surfaces a `Possible Duplicate` (or `Strong Candidate`) card, allows linking both URLs to the same `Job`, and allows unlinking if needed.
- Two separate openings with the same title at the same company (`JR-1001` vs `JR-1002`) are kept distinct.

---

## Phase 3 — LangChain Requirement Extraction & Quote Verification (AI MVP)

**Goal:** Extract explicit job restrictions with verbatim evidence quotes, cache results by content hash, and enforce a strict 15-second timeout.

### Step 3.1: LangChain Extraction Service (`backend/app/services/analyzer.py`)
- Implement model factory supporting configurable provider (`openai`, `anthropic`, `google`, `ollama`) via `ChatModel.with_structured_output(JobRequirementsExtraction)`.
- Craft a tight system prompt instructing the model:
  - Extract only **explicitly stated** restrictions from the provided text.
  - Default any unmentioned category to `not_stated`.
  - Copy `evidence_quote` **character-for-character** from the source text.
  - Never equate general work authorization with U.S. citizenship.
  - Distinguish active security clearance from eligibility to obtain one.
- Wrap the LLM call in `asyncio.wait_for(..., timeout=15.0)`.

### Step 3.2: Deterministic Quote Verifier & Content-Hash Cache
- Run `verify_quote_in_text(slot.evidence_quote, description_text)` on every extracted slot.
- Store validated output in `requirement_analyses` keyed by `(cleaned_text_hash, schema_version, model_provider, model_name)`.
- Include cached analysis automatically in `POST /api/v1/jobs/resolve` responses when a cache hit exists.

### Step 3.3: Eligibility UI in the Extension Side Panel
- Add the **Eligibility & Restrictions** tab/section below the History Card:
  - Shows a one-time external-API consent banner before the first LLM request.
  - Displays loading skeleton with countdown/progress indicator and a 15-second timeout handler (`AbortController`).
  - Renders scannable cards for stated restrictions (`Required` in bold badge, `Preferred` in secondary badge, `Unclear` in warning badge) with expandable verbatim quote blocks.
  - Groups `not_stated` categories into a compact single-line summary at the bottom.

**Phase 3 Done When:**
- Clicking "Analyze Requirements" extracts all stated restrictions with verified quotes in `< 15s`, caches the result for instant reuse on revisit, and never blocks or delays the application history card.

---

## Phase 4 — Reusable Resumes (Text + PDF Ref), User Profile & Comparator

**Goal:** Store the user's 1–4 reusable resumes as lightweight plain text + a PDF reference (`pdf_ref`) in SQLite, link each job application via `resume_id`, auto-populate the user's profile from resume text, recommend which resume to use per job, and flag personal dealbreakers.

### Step 4.1: Reusable Resume Text & Reference Service (`backend/app/services/resume_parser.py`)
- Implement `POST /api/v1/resumes`, `GET /api/v1/resumes`, `PATCH /api/v1/resumes/{resume_id}`, and `DELETE /api/v1/resumes/{resume_id}`.
- Support two ways to add a reusable resume. A chosen PDF is copied into `data/resumes/{resume_id}.pdf`:
  1. **Paste Resume Text:** Save plain text in SQLite when no PDF is attached.
  2. **Upload PDF:** `pypdf` extracts `content_text`, and the service keeps its own PDF copy so the original file can be moved or deleted.
- Parse structured attributes (`skills`, `domains`, `degrees`, `graduation_date`, `years_experience_est`, `certifications`) from `content_text` and provide one-click **"Sync to Profile"**.
- In the Side Panel's **"Confirm Applied"** modal, let the user select which `resume_id` they used from a dropdown (pre-selecting the default or best-matching resume).

### Step 4.2: Rule-Based Profile & Resume Comparator (`backend/app/services/comparator.py`)
- Implement `GET /api/v1/profile` and `PUT /api/v1/profile`.
- Implement deterministic comparison rules for the **Hard Gates**:
  1. **Citizenship:** If `us_citizenship_required == True` and `profile.is_us_citizen == False` -> `possible_mismatch`.
  2. **Sponsorship:** If `sponsorship_offered == "no"` and `profile.requires_visa_sponsorship == True` -> `possible_mismatch`.
  3. **Clearance:** Compare ranked enum (`none < public_trust < secret < top_secret < ts_sci < ts_sci_poly`). If `timing == "active_required"` and `profile.clearance_level < required_level` -> `possible_mismatch`; if `timing == "eligible_to_obtain"` -> `needs_review`.
  4. **Education:** Compare degree rank (`none < associate < bachelor < master < phd`). If `profile.highest_degree < minimum_degree`: returns `needs_review` if `accepts_equivalent_experience == True`, else `possible_mismatch`.
  5. **Graduation Window:** Compare `profile.graduation_date` (`YYYY-MM`) against `[earliest_graduation, latest_graduation]`.
  6. **Experience:** Compare `profile.years_experience_total` against `min_years_required` (`< min - 1` -> `possible_mismatch`, within 1 year -> `needs_review`, `>= min` -> `likely_meets`).
  7. **Work Mode / Location:** Compare `work_mode` against `profile.preferred_work_modes` and `restricted_locations` against `profile.current_state_code` / `willing_to_relocate`.
- Implement **Resume Fit & Skill Coverage**:
  - Compare the job's required `experience.domains` and `other_restrictions` against each uploaded `Resume`.
  - Recommend **which stored resume variant best matches** the current job posting.
  - Highlight any required skill/domain in the job posting that is **missing from the selected resume** so the user knows before applying.
- Ensure any `None` attribute on `UserProfile` outputs `profile_missing`—never a false `possible_mismatch`.

### Step 4.3: History Search, Export/Import & Always-On Docker Setup
- Add a **History & Search** view in the extension (`GET /api/v1/jobs?status=...&q=...`) to browse/filter all saved and applied jobs.
- Add **Export / Import JSON** (`GET /api/v1/export`, `POST /api/v1/import`) for one-click backup of the SQLite database.
- Verify `docker compose up -d` with `restart: unless-stopped` so the backend runs automatically in the background whenever Docker Desktop starts.

**Phase 4 Done When:**
- Setting your local profile immediately flags mismatches (e.g., "No sponsorship offered" or "Active TS/SCI required") in red and compatible gates in green, and you can search/export your entire application database.

---

## Phase 5 — Future Enhancements (Out of MVP Scope)

Only consider after Phases 0–4 are proven in daily use:
- Opt-in confirmation email parser (local `.eml` paste or Gmail read-only match for application confirmation receipts).
- Firefox WebExtension compatibility build.
- **LangGraph** workflow only if adding multi-step resume tailoring or interactive requirement Q&A.
