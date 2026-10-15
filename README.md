# JobRecall

> **Tagline:** Remember where you applied. Understand whether a job fits before applying again.
>
> **Project Target:** A personal-use, local-first Chrome browser extension (Manifest V3 + Side Panel) backed by a Dockerized local Python FastAPI + SQLite + LangChain service that prevents duplicate job applications, stores reusable resume text + PDF references, and extracts explicit, evidence-backed eligibility restrictions from job postings.
>
> **Current Status:** Phases 0–4 Implemented & Tested (`backend/` + `extension/dist/` + `docker-compose.yml`).

---

## 1. Project Target & Core Philosophy

JobRecall solves two daily problems for active job seekers:

1. **Application Memory ("Have I seen or applied to this job already?"):**  
   Job postings appear across multiple boards (LinkedIn, Indeed, Dice) and external ATS portals (Workday, Greenhouse, Lever, Ashby, iCIMS). JobRecall resolves the currently viewed job against your local SQLite database in **under 1 second**—identifying exact matches, cross-posted duplicates, and separate openings that happen to share the same title.
2. **Evidence-Backed Eligibility Triage ("Can I actually apply to this job?"):**  
   Job descriptions bury dealbreakers (U.S. citizenship, no visa sponsorship, active security clearance, graduation date windows, strict years of experience, or on-site residency). On demand, JobRecall uses a single structured LangChain LLM call to extract stated restrictions with **verbatim verified quotes**, comparing hard gates against your local profile without ever letting slow or offline AI block your application history.

### Non-Negotiable Design Rules
- **History First, AI Second:** Application history is a deterministic database lookup (<1s). AI analysis is asynchronous, cached by normalized content hash, and capped at a 15-second timeout.
- **Explicit Confirmation Only:** Clicking an "Apply" button on a job board never automatically marks a job as `applied`. Only user confirmation (or explicit status change) records a submitted application.
- **Zero Hallucinated Disqualifications:** Missing information is labeled `not_stated`, never assumed to be a disqualification. Every AI-extracted restriction must include a verbatim quote verified against the captured posting text.
- **Local-First Privacy:** All application history and user profile data stay in a local SQLite database bound to `127.0.0.1`. External LLM calls are opt-in and only receive cleaned job description text.

---

## 2. Documentation Index

All architectural decisions, schemas, and implementation steps are documented in `docs/`:

- **[`docs/SPECIFICATION.md`](docs/SPECIFICATION.md)** — Complete product specification, UX flows, edge-case refinements (SPA split-view support, ATS redirect bridging, quote verification), and MVP acceptance criteria.
- **[`docs/ARCHITECTURE_AND_SCHEMAS.md`](docs/ARCHITECTURE_AND_SCHEMAS.md)** — Deep technical blueprint: Chrome MV3 Side Panel + Service Worker communication, SQLite database schema, URL/ID normalization rules, duplicate-matching algorithm, FastAPI endpoints, and Pydantic + LangChain schemas.
- **[`docs/IMPLEMENTATION_ROADMAP.md`](docs/IMPLEMENTATION_ROADMAP.md)** — Detailed phase-by-phase build plan (Phases 0–5), directory structure, testing strategy, and local Windows background service setup.

---

## 3. Technology Stack

| Layer | Technology | Role |
|---|---|---|
| **Browser Extension** | TypeScript, Vite, Chrome Manifest V3 (`sidePanel`, `storage`, `activeTab`, `scripting`) | Captures job metadata/text from active tabs, handles SPA URL changes, renders the Side Panel UI, and talks to FastAPI via the Background Service Worker. |
| **Local API Backend** | Python 3.11+, FastAPI, Uvicorn, Docker & Docker Compose | Serves local REST API (`127.0.0.1:8765:8765`), runs automatically in the background (`restart: unless-stopped`), enforces API token auth, and runs deduplication and profile matching logic. |
| **Database & Migrations** | SQLite (WAL mode), SQLAlchemy 2.0, Alembic (persisted via Docker volume `./backend/data:/app/data`) | Stores canonical `Job` records, linked `Posting` URLs/snapshots, `JobEvent` audit history, reusable `Resume` text, cached `RequirementAnalysis`, and `UserProfile`. |
| **Resume Store** | Plain text in SQLite + PDF copy in `backend/data/resumes/` (`pypdf`) | Copies each resume PDF into the local service so it survives if the original file is moved or deleted. Applications link `resume_id` only. |
| **AI Extraction** | LangChain (`with_structured_output`), Pydantic v2 | Executes a single structured-output LLM call (configurable provider: OpenAI, Anthropic, Gemini, or local Ollama) with deterministic post-validation of verbatim quotes. |
| **Testing** | `pytest`, `httpx` (`TestClient`), Vitest | Automated tests for URL normalization, duplicate scoring, CRUD lifecycle, quote verification, and offline fallback. |

---

## 4. Planned Repository Structure

```text
job-recall/
├── README.md
├── docker-compose.yml                # Runs FastAPI backend on 127.0.0.1:8765 with persistent SQLite volume
├── docs/
│   ├── SPECIFICATION.md              # Product spec & refined requirements
│   ├── ARCHITECTURE_AND_SCHEMAS.md   # DB tables, Docker config, Pydantic models, matching rules
│   └── IMPLEMENTATION_ROADMAP.md     # Step-by-step build guide (Phases 0-5)
├── backend/
│   ├── Dockerfile                    # Multi-stage Python 3.11-slim image with auto-migrations
│   ├── entrypoint.sh                 # Runs `alembic upgrade head` then starts Uvicorn
│   ├── pyproject.toml
│   ├── .env.example
│   ├── alembic.ini
│   ├── data/                         # Host-mounted directory for jobrecall.db (+ WAL/SHM files)
│   ├── migrations/
│   ├── app/
│   │   ├── main.py                   # FastAPI entrypoint & CORS/Auth middleware
│   │   ├── config.py                 # Pydantic Settings (.env loader)
│   │   ├── database.py               # SQLite engine (WAL mode) & session factory
│   │   ├── models/                   # SQLAlchemy ORM models
│   │   ├── schemas/                  # Pydantic API & LangChain schemas
│   │   ├── api/                      # Route handlers (/jobs, /resumes, /analyze, /profile, /health)
│   │   └── services/
│   │       ├── normalizer.py         # URL, company, title, location, & text cleaner
│   │       ├── matcher.py            # Tiered duplicate detection engine
│   │       ├── resume_parser.py      # Resume text/PDF extractor & skill/profile sync
│   │       ├── analyzer.py           # LangChain extraction + quote verifier + cache
│   │       └── comparator.py         # Profile + Resume text vs. Job requirement evaluator
│   └── tests/
│       ├── fixtures/                 # Sample job postings & edge cases
│       ├── test_normalizer.py
│       ├── test_matcher.py
│       ├── test_jobs_api.py
│       └── test_analyzer.py
└── extension/
    ├── package.json
    ├── tsconfig.json
    ├── vite.config.ts
    ├── sidepanel.html
    ├── public/
    │   └── manifest.json             # Chrome Manifest V3
    └── src/
        ├── background/               # Service Worker (API client, tab bridge, auth)
        ├── content/                  # Content scripts & site adapters + manual selection
        ├── sidepanel/                # Persistent Side Panel UI (History + Eligibility)
        └── shared/                   # TypeScript types & message contracts
```

---

## 5. Quickstart Guide

### A. Start the Backend (Option 1: Docker Compose — Recommended)

1. Configure your environment file in `backend/.env` (created from `backend/.env.example`):
   - Set `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY`, or use `LLM_PROVIDER=ollama` / `LLM_PROVIDER=mock`).
   - Keep or customize `JOBRECALL_API_TOKEN` (default: `jobrecall-local-dev-token-2026`).
2. Start the always-on local container (bound strictly to `127.0.0.1:8765`):
   ```powershell
   docker compose up --build -d
   ```
3. Check service health:
   ```powershell
   curl http://127.0.0.1:8765/api/v1/health
   ```

### B. Start the Backend (Option 2: Local Python Virtualenv)

```powershell
python -m venv backend/.venv
.\backend\.venv\Scripts\pip install -e "backend[dev]"
Set-Location backend
.\.venv\Scripts\alembic upgrade head
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

### C. Build & Load the Chrome Extension

1. Build the extension bundle:
   ```powershell
   cd extension
   npm install
   npm run build
   ```
2. Open Chrome and navigate to `chrome://extensions/`.
3. Enable **Developer mode** (top-right toggle).
4. Click **Load unpacked** and select the `extension/dist` folder.
5. Click the **JobRecall** icon in your Chrome toolbar to open the persistent **Side Panel** (or click **Capture Tab** inside the Side Panel at any time; the extension automatically re-injects `content.js` into already-open tabs after an extension reload and supports multi-frame ATS DOM extraction).

### D. Run Automated Tests

```powershell
.\backend\.venv\Scripts\pytest backend/tests -v
```

