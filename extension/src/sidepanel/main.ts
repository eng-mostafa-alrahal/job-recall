import type {
  AnalyzeJobResponse,
  CandidateMatch,
  ExtensionSettings,
  ExtractedPagePosting,
  JobRead,
  JobStatus,
  ProfileFitLabel,
  ResolveJobResponse,
  ResumeRead,
  UserProfileRead,
} from "../shared/types";

let currentPosting: ExtractedPagePosting = {
  source_url: "",
  company: "",
  title: "",
  location: "",
  requisition_id: "",
  description_text: "",
};
let currentMatchedJob: JobRead | null = null;
let cachedResumes: ResumeRead[] = [];
let currentSettings: ExtensionSettings | null = null;

function $<T extends HTMLElement = HTMLElement>(id: string): T {
  return document.getElementById(id) as T;
}

function escapeHtml(str: string | null | undefined): string {
  return (str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "Unknown date";
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  } catch {
    return iso;
  }
}

async function apiCall<T = unknown>(
  path: string,
  method = "GET",
  body?: unknown,
  timeoutMs = 15500
): Promise<{ ok: boolean; status: number; data: T }> {
  return chrome.runtime.sendMessage({
    type: "API_REQUEST",
    path,
    method,
    body,
    timeoutMs,
  });
}

function showGlobalError(msg: string | null): void {
  const el = $("global-alert");
  if (!msg) {
    el.classList.add("hidden");
    el.textContent = "";
  } else {
    el.textContent = msg;
    el.classList.remove("hidden");
  }
}

async function checkHealth(): Promise<boolean> {
  const res = await apiCall<{
    status: string;
    auth_valid: boolean;
    ai_configured: boolean;
    llm_provider: string;
    llm_model: string;
  }>("/api/v1/health", "GET", undefined, 4000);

  const dot = $("health-dot");
  const label = $("health-label");

  if (!res.ok) {
    dot.className = "brand-dot offline";
    label.textContent = "Backend Offline";
    showGlobalError(
      "Local backend is offline at 127.0.0.1:8765. Start Docker (`docker compose up -d`) or uvicorn."
    );
    return false;
  }

  if (!res.data.auth_valid) {
    dot.className = "brand-dot";
    label.textContent = "Token Mismatch";
    showGlobalError(
      "X-JobRecall-Token mismatch. Check the History > Local API Connection Settings tab."
    );
    return false;
  }

  dot.className = "brand-dot online";
  label.textContent = res.data.ai_configured
    ? `Local OK • ${res.data.llm_provider}`
    : "Local OK • AI Off";
  showGlobalError(null);
  return true;
}

function syncFormFromPosting(p: ExtractedPagePosting): void {
  $<HTMLInputElement>("input-company").value = p.company || "";
  $<HTMLInputElement>("input-title").value = p.title || "";
  $<HTMLInputElement>("input-location").value = p.location || "";
  $<HTMLInputElement>("input-req-id").value = p.requisition_id || "";
  $<HTMLTextAreaElement>("input-description").value = p.description_text || "";
  $("desc-char-count").textContent = String((p.description_text || "").length);
  $("captured-platform").textContent = p.platform || "web";
}

function readPostingFromForm(): ExtractedPagePosting {
  return {
    ...currentPosting,
    company: $<HTMLInputElement>("input-company").value.trim() || "Unknown Company",
    title: $<HTMLInputElement>("input-title").value.trim() || "Untitled Position",
    location: $<HTMLInputElement>("input-location").value.trim() || null,
    requisition_id: $<HTMLInputElement>("input-req-id").value.trim() || null,
    description_text: $<HTMLTextAreaElement>("input-description").value.trim() || null,
  };
}

function renderJobDetails(job: JobRead | null): void {
  currentMatchedJob = job;
  const statusPill = $("current-status-pill");
  const detailsBox = $("tracked-job-details");
  const saveBtn = $("btn-save-job");
  const applyModalBtn = $("btn-open-apply-modal");

  if (!job) {
    statusPill.textContent = "not tracked";
    statusPill.className = "badge";
    detailsBox.classList.add("hidden");
    saveBtn.textContent = "Save for Later";
    applyModalBtn.textContent = "Confirm Applied";
    return;
  }

  statusPill.textContent = job.status.replace("_", " ").toUpperCase();
  statusPill.className =
    job.status === "applied" || job.status === "offer" || job.status === "interviewing"
      ? "badge badge-green"
      : job.status === "rejected"
      ? "badge badge-red"
      : "badge badge-amber";

  saveBtn.textContent = "Update Status";
  applyModalBtn.textContent =
    job.status === "applied" ? "Update Application" : "Confirm Applied";

  detailsBox.classList.remove("hidden");
  const summaryLines = [
    `<strong>Status:</strong> ${escapeHtml(job.status)}`,
    job.applied_at ? `<strong>Applied on:</strong> ${formatDate(job.applied_at)}` : "",
    job.resume_label
      ? `<strong>Resume Used:</strong> ${escapeHtml(job.resume_label)} ${
          job.resume_pdf_ref
            ? `<span class="muted-xs">(${escapeHtml(job.resume_pdf_ref)})</span>`
            : ""
        }`
      : "",
    job.confirmation_ref
      ? `<strong>Confirmation Ref:</strong> ${escapeHtml(job.confirmation_ref)}`
      : "",
    job.employer_req_id
      ? `<strong>Requisition ID:</strong> ${escapeHtml(job.employer_req_id)}`
      : "",
  ].filter(Boolean);
  $("tracked-meta-summary").innerHTML = summaryLines.join("<br/>");

  // Linked postings
  $("postings-count").textContent = String(job.postings.length);
  const postingsEl = $("linked-postings-list");
  postingsEl.innerHTML = job.postings
    .map(
      (p) => `
      <div class="req-card">
        <div class="section-header">
          <strong>${escapeHtml(p.platform)}</strong>
          ${
            job.postings.length > 1
              ? `<button class="btn btn-sm btn-outline" data-unlink-id="${escapeHtml(p.id)}">Unlink</button>`
              : `<span class="muted-xs">${formatDate(p.last_seen_at)}</span>`
          }
        </div>
        <div class="muted-xs" style="word-break:break-all;">${escapeHtml(p.source_url_norm)}</div>
      </div>
    `
    )
    .join("");

  postingsEl.querySelectorAll<HTMLButtonElement>("[data-unlink-id]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const pid = btn.getAttribute("data-unlink-id");
      if (!pid || !currentMatchedJob) return;
      const res = await apiCall<JobRead>(
        `/api/v1/jobs/${currentMatchedJob.id}/unlink-posting/${pid}`,
        "POST"
      );
      if (res.ok) {
        renderJobDetails(res.data);
      }
    });
  });

  // Events timeline
  $("events-timeline").innerHTML = job.events
    .map(
      (e) => `
      <div class="timeline-item">
        <div><strong>${escapeHtml(e.event_type.replace("_", " "))}</strong>: ${escapeHtml(e.note || "")}</div>
        <div class="timeline-time">${formatDate(e.created_at)}</div>
      </div>
    `
    )
    .join("");
}

function renderCandidates(candidates: CandidateMatch[]): void {
  const sec = $("duplicates-section");
  const list = $("duplicates-list");
  if (!candidates || candidates.length === 0) {
    sec.classList.add("hidden");
    list.innerHTML = "";
    return;
  }

  sec.classList.remove("hidden");
  list.innerHTML = candidates
    .map(
      (c) => `
      <div class="req-card">
        <div class="section-header">
          <strong>${escapeHtml(c.job.title_raw)} @ ${escapeHtml(c.job.company_raw)}</strong>
          <span class="badge badge-amber">${Math.round(c.confidence * 100)}% match</span>
        </div>
        <div class="muted-xs">
          Status: <strong>${escapeHtml(c.job.status)}</strong> • ${escapeHtml(c.reasons.join("; "))}
        </div>
        <div class="row-2" style="margin-top:4px;">
          <button class="btn btn-sm btn-primary" data-link-job="${escapeHtml(c.job.id)}">
            Same Job — Link Posting
          </button>
          <button class="btn btn-sm btn-outline" data-dismiss-job="${escapeHtml(c.job.id)}">
            Different Opening
          </button>
        </div>
      </div>
    `
    )
    .join("");

  list.querySelectorAll<HTMLButtonElement>("[data-link-job]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const jid = btn.getAttribute("data-link-job");
      if (!jid) return;
      const payload = readPostingFromForm();
      const res = await apiCall<JobRead>(`/api/v1/jobs/${jid}/link-posting`, "POST", payload);
      if (res.ok) {
        await resolveCurrentForm();
      }
    });
  });

  list.querySelectorAll<HTMLButtonElement>("[data-dismiss-job]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const jid = btn.getAttribute("data-dismiss-job");
      if (!jid) return;
      const payload = readPostingFromForm();
      await apiCall(`/api/v1/jobs/${jid}/dismiss-duplicate`, "POST", {
        source_url: payload.source_url,
        platform: payload.platform,
        source_posting_id: payload.source_posting_id,
      });
      await resolveCurrentForm();
    });
  });
}

function renderResolveOutcome(res: ResolveJobResponse): void {
  const banner = $("match-banner");
  const title = $("match-title");
  const sub = $("match-subtitle");

  $("captured-platform").textContent = res.normalized_platform;
  if (res.extracted_req_id && !$<HTMLInputElement>("input-req-id").value) {
    $<HTMLInputElement>("input-req-id").value = res.extracted_req_id;
  }

  if (res.match_state === "previously_applied" && res.matched_job) {
    banner.className = "match-banner match-applied";
    title.textContent = `Previously Applied (${res.matched_job.status.toUpperCase()})`;
    sub.textContent = `Applied on ${formatDate(res.matched_job.applied_at)} • ${
      res.matched_job.resume_label
        ? `Resume: ${res.matched_job.resume_label}`
        : "Confirmed in local database"
    }`;
    renderJobDetails(res.matched_job);
    renderCandidates([]);
  } else if (res.match_state === "saved_in_progress" && res.matched_job) {
    banner.className = "match-banner match-saved";
    title.textContent = `Saved / In Progress (${res.matched_job.status})`;
    sub.textContent = `First saved ${formatDate(res.matched_job.created_at)} — not marked as applied yet.`;
    renderJobDetails(res.matched_job);
    renderCandidates([]);
  } else if (res.match_state === "possible_duplicate") {
    banner.className = "match-banner match-duplicate";
    title.textContent = `Possible Duplicate Found (${res.candidate_matches.length})`;
    sub.textContent =
      "Similar posting exists in your history. Inspect below to link or keep separate.";
    renderJobDetails(null);
    renderCandidates(res.candidate_matches);
  } else {
    banner.className = "match-banner match-neutral";
    title.textContent = "No Local Record Found";
    sub.textContent =
      "No matching job in your local database. Save or mark as applied below.";
    renderJobDetails(null);
    renderCandidates([]);
  }

  // If resolve returned a cached AI analysis for this description hash, render it immediately!
  if (res.cached_analysis) {
    renderAnalysisResult(res.cached_analysis);
  }
}

async function resolveCurrentForm(): Promise<void> {
  const payload = readPostingFromForm();
  if (!payload.source_url) return;
  const res = await apiCall<ResolveJobResponse>("/api/v1/jobs/resolve", "POST", payload);
  if (res.ok && res.data) {
    renderResolveOutcome(res.data);
  }
}

function fitBadgeHtml(label: ProfileFitLabel): string {
  switch (label) {
    case "likely_meets":
      return `<span class="badge badge-green">Likely Meets</span>`;
    case "possible_mismatch":
      return `<span class="badge badge-red">Possible Mismatch</span>`;
    case "needs_review":
      return `<span class="badge badge-amber">Needs Review</span>`;
    case "profile_missing":
      return `<span class="badge">Profile Missing</span>`;
    default:
      return `<span class="badge">Not Applicable</span>`;
  }
}

function renderAnalysisResult(data: AnalyzeJobResponse): void {
  $("analysis-error").classList.add("hidden");
  $("analysis-results").classList.remove("hidden");

  const overallBanner = $("overall-fit-banner");
  const overall = data.comparison.overall_fit;
  overallBanner.className =
    overall === "possible_mismatch"
      ? "fit-banner match-duplicate"
      : overall === "likely_meets"
      ? "fit-banner match-applied"
      : "fit-banner match-saved";
  overallBanner.innerHTML = `Overall Profile Check: ${fitBadgeHtml(overall)} <span class="muted-xs" style="float:right;">${
    data.cached ? "Cached" : `${data.latency_ms}ms`
  } • ${escapeHtml(data.model_name)}</span>`;

  // Recommended Resume Card
  const rec = data.comparison.resume_recommendation;
  const recBox = $("resume-rec-card");
  if (rec && rec.recommended_resume_label) {
    recBox.classList.remove("hidden");
    recBox.innerHTML = `
      <strong>Recommended Resume:</strong> ${escapeHtml(rec.recommended_resume_label)}
      ${
        rec.matched_skills.length > 0
          ? `<div class="muted-xs">Matched skills: ${escapeHtml(rec.matched_skills.join(", "))}</div>`
          : ""
      }
      ${
        rec.missing_skills.length > 0
          ? `<div class="muted-xs" style="color:#f87171;">Missing from resume: ${escapeHtml(
              rec.missing_skills.join(", ")
            )}</div>`
          : ""
      }
    `;
  } else {
    recBox.classList.add("hidden");
  }

  // Contradictions
  const contraBox = $("contradictions-box");
  if (data.extraction.contradictions_or_ambiguities?.length > 0) {
    contraBox.classList.remove("hidden");
    contraBox.innerHTML = `<strong>Ambiguity / Contradiction:</strong> ${escapeHtml(
      data.extraction.contradictions_or_ambiguities.join(" | ")
    )}`;
  } else {
    contraBox.classList.add("hidden");
  }

  // Category slots
  const categories: Array<{ key: keyof AnalyzeJobResponse["extraction"]; title: string }> = [
    { key: "work_authorization", title: "Work Authorization & Citizenship" },
    { key: "sponsorship", title: "Visa Sponsorship" },
    { key: "security_clearance", title: "Security Clearance" },
    { key: "education", title: "Education" },
    { key: "graduation_window", title: "Graduation Window" },
    { key: "experience", title: "Experience" },
    { key: "location_work_mode", title: "Location & Work Mode" },
  ];

  const statedHtml: string[] = [];
  const notStatedLabels: string[] = [];

  for (const cat of categories) {
    const slot = data.extraction[cat.key] as {
      status: string;
      summary: string | null;
      evidence_quote: string | null;
      quote_verified: boolean;
    };
    if (!slot || slot.status === "not_stated") {
      notStatedLabels.push(cat.title);
      continue;
    }
    const evalItem = data.comparison.evaluations[cat.key as string];
    statedHtml.push(`
      <div class="req-card">
        <div class="section-header">
          <strong>${escapeHtml(cat.title)}</strong>
          <div>
            <span class="badge">${escapeHtml(slot.status.toUpperCase())}</span>
            ${evalItem ? fitBadgeHtml(evalItem.fit_label) : ""}
          </div>
        </div>
        <div>${escapeHtml(slot.summary || "")}</div>
        ${
          evalItem
            ? `<div class="muted-xs">${escapeHtml(evalItem.reason)}</div>`
            : ""
        }
        ${
          slot.evidence_quote
            ? `<div class="quote-box">"${escapeHtml(slot.evidence_quote)}" ${
                slot.quote_verified
                  ? `<span class="muted-xs">(verified)</span>`
                  : `<span class="badge badge-amber">unverified quote</span>`
              }</div>`
            : ""
        }
      </div>
    `);
  }

  for (const other of data.extraction.other_restrictions || []) {
    statedHtml.push(`
      <div class="req-card">
        <div class="section-header">
          <strong>${escapeHtml(other.category_label)}</strong>
          <span class="badge">${escapeHtml(other.status.toUpperCase())}</span>
        </div>
        <div>${escapeHtml(other.summary)}</div>
        <div class="quote-box">"${escapeHtml(other.evidence_quote)}"</div>
      </div>
    `);
  }

  $("requirements-cards").innerHTML =
    statedHtml.length > 0
      ? statedHtml.join("")
      : `<div class="muted-sm">No explicit restrictions were stated in this job description.</div>`;

  $("not-stated-footer").textContent =
    notStatedLabels.length > 0
      ? `Not stated in posting: ${notStatedLabels.join(", ")}`
      : "All 7 core categories were explicitly addressed in the posting.";
}

async function runEligibilityAnalysis(forceRefresh = false): Promise<void> {
  if (!currentSettings?.aiConsentAccepted) {
    $("ai-consent-card").classList.remove("hidden");
    return;
  }

  const payload = readPostingFromForm();
  if (!payload.description_text || payload.description_text.length < 40) {
    const errEl = $("analysis-error");
    errEl.textContent =
      "Not enough job description text captured (minimum 40 chars). Expand 'Job Description Text' on the Current Job tab and paste the posting text.";
    errEl.classList.remove("hidden");
    return;
  }

  $("analysis-error").classList.add("hidden");
  $("analysis-loading").classList.remove("hidden");

  const res = await apiCall<AnalyzeJobResponse>(
    "/api/v1/jobs/analyze",
    "POST",
    {
      description_text: payload.description_text,
      resume_id: currentMatchedJob?.resume_id || null,
      force_refresh: forceRefresh,
    },
    15500
  );

  $("analysis-loading").classList.add("hidden");

  if (!res.ok) {
    const errDetail = (res.data as { detail?: { message?: string } })?.detail;
    const errEl = $("analysis-error");
    errEl.textContent =
      errDetail?.message || "Analysis unavailable. Check LLM settings or retry.";
    errEl.classList.remove("hidden");
    return;
  }

  renderAnalysisResult(res.data);
}

async function loadResumesAndProfile(): Promise<void> {
  const [resResp, profResp] = await Promise.all([
    apiCall<ResumeRead[]>("/api/v1/resumes"),
    apiCall<UserProfileRead>("/api/v1/profile"),
  ]);

  if (resResp.ok && Array.isArray(resResp.data)) {
    cachedResumes = resResp.data;
    const selectEl = $<HTMLSelectElement>("select-resume");
    selectEl.innerHTML =
      `<option value="">-- Unspecified --</option>` +
      cachedResumes
        .map(
          (r) =>
            `<option value="${escapeHtml(r.id)}" ${
              r.is_default ? "selected" : ""
            }>${escapeHtml(r.label)} ${
              r.target_role ? `(${escapeHtml(r.target_role)})` : ""
            }</option>`
        )
        .join("");

    const listEl = $("resumes-list");
    listEl.innerHTML =
      cachedResumes.length === 0
        ? `<div class="muted-xs">No resumes stored yet. Add your reusable resume text + PDF link above.</div>`
        : cachedResumes
            .map(
              (r) => `
          <div class="req-card">
            <div class="section-header">
              <strong>${escapeHtml(r.label)} ${
                r.is_default ? `<span class="badge badge-green">Default</span>` : ""
              }</strong>
              <button class="btn btn-sm btn-outline" data-del-resume="${escapeHtml(r.id)}">Delete</button>
            </div>
            ${
              r.has_stored_pdf
                ? `<div class="muted-xs"><strong>Stored PDF:</strong> ${escapeHtml(
                    r.pdf_ref || "resume.pdf"
                  )}${
                    r.pdf_size_bytes
                      ? ` (${Math.max(1, Math.round(r.pdf_size_bytes / 1024))} KB)`
                      : ""
                  }
                  <button class="btn btn-sm btn-outline" data-dl-resume="${escapeHtml(
                    r.id
                  )}" data-dl-name="${escapeHtml(r.pdf_ref || "resume.pdf")}">Download</button>
                  </div>`
                : `<div class="muted-xs">No PDF copy stored. Add the resume again with the PDF file.</div>`
            }
            <div class="muted-xs">
              <strong>Parsed Skills:</strong> ${escapeHtml(
                r.parsed.skills.join(", ") || "None detected"
              )}
            </div>
          </div>
        `
            )
            .join("");

    listEl.querySelectorAll<HTMLButtonElement>("[data-dl-resume]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const rid = btn.getAttribute("data-dl-resume");
        const name = btn.getAttribute("data-dl-name") || "resume.pdf";
        if (!rid || !currentSettings) return;
        const resp = await fetch(
          `${currentSettings.apiBaseUrl.replace(/\/$/, "")}/api/v1/resumes/${rid}/pdf`,
          { headers: { "X-JobRecall-Token": currentSettings.apiToken } }
        );
        if (!resp.ok) {
          showGlobalError("Stored PDF is not available. Upload the resume PDF again.");
          return;
        }
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = name;
        a.click();
        URL.revokeObjectURL(url);
      });
    });

    listEl.querySelectorAll<HTMLButtonElement>("[data-del-resume]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const rid = btn.getAttribute("data-del-resume");
        if (!rid) return;
        await apiCall(`/api/v1/resumes/${rid}`, "DELETE");
        await loadResumesAndProfile();
      });
    });
  }

  if (profResp.ok && profResp.data) {
    const p = profResp.data;
    $<HTMLSelectElement>("prof-citizen").value =
      p.is_us_citizen === null ? "" : String(p.is_us_citizen);
    $<HTMLSelectElement>("prof-work-auth").value =
      p.authorized_to_work_us === null ? "" : String(p.authorized_to_work_us);
    $<HTMLSelectElement>("prof-sponsorship").value =
      p.requires_visa_sponsorship === null ? "" : String(p.requires_visa_sponsorship);
    $<HTMLSelectElement>("prof-clearance").value = p.clearance_level || "none";
    $<HTMLSelectElement>("prof-degree").value = p.highest_degree || "";
    $<HTMLInputElement>("prof-grad").value = p.graduation_date || "";
    $<HTMLInputElement>("prof-years").value =
      p.years_experience_total !== null ? String(p.years_experience_total) : "";
  }
}

async function loadHistoryList(): Promise<void> {
  const q = $<HTMLInputElement>("history-search-q").value.trim();
  const status = $<HTMLSelectElement>("history-filter-status").value;
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (status) params.set("status", status);

  const res = await apiCall<JobRead[]>(`/api/v1/jobs?${params.toString()}`);
  const listEl = $("history-jobs-list");
  if (!res.ok || !Array.isArray(res.data)) {
    listEl.innerHTML = `<div class="muted-xs">Unable to load history.</div>`;
    return;
  }
  if (res.data.length === 0) {
    listEl.innerHTML = `<div class="muted-xs">No saved or applied jobs match your filter.</div>`;
    return;
  }

  listEl.innerHTML = res.data
    .map(
      (j) => `
      <div class="req-card">
        <div class="section-header">
          <strong>${escapeHtml(j.title_raw)}</strong>
          <span class="badge ${
            j.status === "applied" ? "badge-green" : "badge-amber"
          }">${escapeHtml(j.status)}</span>
        </div>
        <div class="muted-xs">
          ${escapeHtml(j.company_raw)} ${
        j.location_raw ? `• ${escapeHtml(j.location_raw)}` : ""
      }
        </div>
        <div class="muted-xs">
          ${
            j.applied_at
              ? `Applied ${formatDate(j.applied_at)}`
              : `Saved ${formatDate(j.created_at)}`
          }
          ${j.resume_label ? ` • Resume: ${escapeHtml(j.resume_label)}` : ""}
        </div>
      </div>
    `
    )
    .join("");
}

async function captureAndResolveActiveTab(isRetry = false): Promise<void> {
  const res = await chrome.runtime.sendMessage({ type: "CAPTURE_ACTIVE_TAB" });
  if (res?.ok && res.payload) {
    currentPosting = res.payload;
    syncFormFromPosting(currentPosting);
    await resolveCurrentForm();

    // If SPA job description is still rendering (< 80 chars), retry once after 750ms
    if (
      !isRetry &&
      (!currentPosting.description_text ||
        currentPosting.description_text.length < 80)
    ) {
      window.setTimeout(() => {
        captureAndResolveActiveTab(true);
      }, 750);
    }

    // Check if the 2-Step Apply Redirect Bridge detected a recent job board tab
    const banner = $("redirect-bridge-banner");
    if (res.redirectSource?.posting) {
      const boardPost: ExtractedPagePosting = res.redirectSource.posting;
      banner.classList.remove("hidden");
      banner.innerHTML = `
        <div><strong>Redirected from ${escapeHtml(boardPost.platform || "Job Board")}?</strong></div>
        <div class="muted-xs">You recently viewed <em>${escapeHtml(boardPost.title)}</em> at <em>${escapeHtml(
        boardPost.company
      )}</em>.</div>
      `;
    } else {
      banner.classList.add("hidden");
    }
  } else {
    $("match-banner").className = "match-banner match-neutral";
    $("match-title").textContent = "No Web Tab Active";
    $("match-subtitle").textContent =
      "Switch to a job posting tab or click 'Capture Tab' above.";
  }
}

async function initSidePanel(): Promise<void> {
  // Load extension settings
  const setRes = await chrome.runtime.sendMessage({ type: "GET_SETTINGS" });
  currentSettings = setRes?.settings;
  if (currentSettings) {
    $<HTMLInputElement>("set-api-url").value = currentSettings.apiBaseUrl;
    $<HTMLInputElement>("set-api-token").value = currentSettings.apiToken;
    if (!currentSettings.aiConsentAccepted) {
      $("ai-consent-card").classList.remove("hidden");
    }
  }

  // Tab navigation
  document.querySelectorAll<HTMLButtonElement>(".tab").forEach((tabBtn) => {
    tabBtn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.add("hidden"));
      tabBtn.classList.add("active");
      const target = tabBtn.getAttribute("data-tab");
      if (target) {
        $(`tab-${target}`).classList.remove("hidden");
        if (target === "resumes") loadResumesAndProfile();
        if (target === "history") loadHistoryList();
      }
    });
  });

  // Topbar Capture button & manual resolve button
  $("btn-recapture").addEventListener("click", () => captureAndResolveActiveTab(false));
  $("btn-resolve-manual").addEventListener("click", resolveCurrentForm);
  $<HTMLTextAreaElement>("input-description").addEventListener("input", (e) => {
    const val = (e.target as HTMLTextAreaElement).value || "";
    $("desc-char-count").textContent = String(val.length);
  });

  // Save for Later button
  $("btn-save-job").addEventListener("click", async () => {
    if (currentMatchedJob) {
      $("apply-confirm-panel").classList.toggle("hidden");
      return;
    }
    const payload = readPostingFromForm();
    const res = await apiCall<JobRead>("/api/v1/jobs", "POST", {
      ...payload,
      status: "saved",
    });
    if (res.ok) {
      await resolveCurrentForm();
    }
  });

  // Open Confirm Applied modal
  $("btn-open-apply-modal").addEventListener("click", async () => {
    await loadResumesAndProfile();
    $<HTMLSelectElement>("select-status").value = "applied";
    $("apply-confirm-panel").classList.remove("hidden");
  });

  $("btn-cancel-status").addEventListener("click", () => {
    $("apply-confirm-panel").classList.add("hidden");
  });

  // Submit status / Confirm Applied
  $("btn-submit-status").addEventListener("click", async () => {
    const statusVal = $<HTMLSelectElement>("select-status").value as JobStatus;
    const resumeId = $<HTMLSelectElement>("select-resume").value || null;
    const note = $<HTMLInputElement>("input-confirm-note").value.trim() || null;

    if (currentMatchedJob) {
      await apiCall<JobRead>(`/api/v1/jobs/${currentMatchedJob.id}/status`, "PATCH", {
        status: statusVal,
        resume_id: resumeId,
        note,
      });
    } else {
      const payload = readPostingFromForm();
      await apiCall<JobRead>("/api/v1/jobs", "POST", {
        ...payload,
        status: statusVal,
        resume_id: resumeId,
        note,
      });
    }
    $("apply-confirm-panel").classList.add("hidden");
    $<HTMLInputElement>("input-confirm-note").value = "";
    await resolveCurrentForm();
  });

  // Add note
  $("btn-add-note").addEventListener("click", async () => {
    const noteInput = $<HTMLInputElement>("input-new-note");
    const txt = noteInput.value.trim();
    if (!txt || !currentMatchedJob) return;
    const res = await apiCall<JobRead>(
      `/api/v1/jobs/${currentMatchedJob.id}/notes`,
      "POST",
      { note: txt }
    );
    if (res.ok) {
      noteInput.value = "";
      renderJobDetails(res.data);
    }
  });

  // AI Consent & Analyze buttons
  $("btn-accept-consent").addEventListener("click", async () => {
    const updated = await chrome.runtime.sendMessage({
      type: "SAVE_SETTINGS",
      settings: { aiConsentAccepted: true },
    });
    currentSettings = updated.settings;
    $("ai-consent-card").classList.add("hidden");
    await runEligibilityAnalysis(false);
  });

  $("btn-analyze").addEventListener("click", () => runEligibilityAnalysis(false));

  // Resumes tab handlers
  $("btn-toggle-new-resume").addEventListener("click", () => {
    $("new-resume-form").classList.toggle("hidden");
  });

  const pdfFileInput = $<HTMLInputElement>("res-pdf-file");
  const fileDropzone = $("res-file-dropzone");
  const fileNameSpan = $("res-file-name");
  const clearPdfBtn = $("btn-clear-pdf");

  function resetPdfSelection(): void {
    pdfFileInput.value = "";
    fileNameSpan.textContent = "No PDF selected";
    fileDropzone.classList.remove("has-file");
    clearPdfBtn.classList.add("hidden");
  }

  pdfFileInput.addEventListener("change", () => {
    const file = pdfFileInput.files?.[0];
    if (!file) {
      resetPdfSelection();
      return;
    }
    const sizeKb = Math.max(1, Math.round(file.size / 1024));
    fileNameSpan.textContent = `${file.name} (${sizeKb} KB)`;
    fileDropzone.classList.add("has-file");
    clearPdfBtn.classList.remove("hidden");

    const labelInput = $<HTMLInputElement>("res-label");
    if (!labelInput.value.trim()) {
      labelInput.value = file.name.replace(/\.pdf$/i, "").replace(/[_-]+/g, " ");
    }
  });

  clearPdfBtn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    resetPdfSelection();
  });

  $("btn-save-resume").addEventListener("click", async () => {
    const label = $<HTMLInputElement>("res-label").value.trim();
    const role = $<HTMLInputElement>("res-role").value.trim() || null;
    const text = $<HTMLTextAreaElement>("res-text").value.trim();
    const isDefault = $<HTMLInputElement>("res-default").checked;
    const pdfFile = pdfFileInput.files?.[0];

    if (!label) return;
    if (!pdfFile && text.length < 10) {
      showGlobalError("Add a PDF file or paste at least 10 characters of resume text.");
      return;
    }

    if (pdfFile && currentSettings) {
      const form = new FormData();
      form.append("file", pdfFile);
      form.append("label", label);
      if (role) form.append("target_role", role);
      form.append("pdf_ref", pdfFile.name);
      if (text.length >= 10) form.append("content_text", text);
      form.append("is_default", String(isDefault));
      form.append("auto_sync_profile", String(isDefault));

      const resp = await fetch(
        `${currentSettings.apiBaseUrl.replace(/\/$/, "")}/api/v1/resumes/extract-pdf`,
        {
          method: "POST",
          headers: { "X-JobRecall-Token": currentSettings.apiToken },
          body: form,
        }
      );
      if (!resp.ok) {
        showGlobalError("Could not store the resume PDF. Check that the file is a readable PDF under 15 MB.");
        return;
      }
    } else if (text.length >= 10) {
      const saved = await apiCall("/api/v1/resumes", "POST", {
        label,
        target_role: role,
        content_text: text,
        is_default: isDefault,
        auto_sync_profile: isDefault,
      });
      if (!saved.ok) {
        showGlobalError("Could not save the resume text.");
        return;
      }
    }

    $("new-resume-form").classList.add("hidden");
    $<HTMLInputElement>("res-label").value = "";
    $<HTMLInputElement>("res-role").value = "";
    $<HTMLTextAreaElement>("res-text").value = "";
    resetPdfSelection();
    showGlobalError(null);
    await loadResumesAndProfile();
  });

  // Save Local Profile
  $("btn-save-profile").addEventListener("click", async () => {
    const citVal = $<HTMLSelectElement>("prof-citizen").value;
    const authVal = $<HTMLSelectElement>("prof-work-auth").value;
    const sponsVal = $<HTMLSelectElement>("prof-sponsorship").value;
    const yrsVal = $<HTMLInputElement>("prof-years").value;

    await apiCall("/api/v1/profile", "PUT", {
      is_us_citizen: citVal === "" ? null : citVal === "true",
      authorized_to_work_us: authVal === "" ? null : authVal === "true",
      requires_visa_sponsorship: sponsVal === "" ? null : sponsVal === "true",
      clearance_level: $<HTMLSelectElement>("prof-clearance").value || "none",
      highest_degree: $<HTMLSelectElement>("prof-degree").value || null,
      graduation_date: $<HTMLInputElement>("prof-grad").value.trim() || null,
      years_experience_total: yrsVal ? parseFloat(yrsVal) : null,
    });
    await loadResumesAndProfile();
  });

  // History Search & Export
  $("history-search-q").addEventListener("input", () => loadHistoryList());
  $("history-filter-status").addEventListener("change", () => loadHistoryList());
  $("btn-export-json").addEventListener("click", async () => {
    const res = await apiCall("/api/v1/export");
    if (res.ok && res.data) {
      const blob = new Blob([JSON.stringify(res.data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `jobrecall-backup-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    }
  });

  // Save Connection Settings
  $("btn-save-settings").addEventListener("click", async () => {
    const apiBaseUrl = $<HTMLInputElement>("set-api-url").value.trim();
    const apiToken = $<HTMLInputElement>("set-api-token").value.trim();
    const updated = await chrome.runtime.sendMessage({
      type: "SAVE_SETTINGS",
      settings: { apiBaseUrl, apiToken },
    });
    currentSettings = updated.settings;
    await checkHealth();
  });

  // Listen for SPA job changes from Content Script / Background
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg?.type === "SIDEPANEL_JOB_CHANGED" && msg.payload) {
      currentPosting = msg.payload;
      syncFormFromPosting(currentPosting);
      resolveCurrentForm();
    } else if (
      msg?.type === "ACTIVE_TAB_URL_CHANGED" ||
      msg?.type === "ACTIVE_TAB_ACTIVATED"
    ) {
      captureAndResolveActiveTab();
    }
  });

  const healthy = await checkHealth();
  if (healthy) {
    await loadResumesAndProfile();
    await captureAndResolveActiveTab();
  }
}

document.addEventListener("DOMContentLoaded", initSidePanel);
