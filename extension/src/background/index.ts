import {
  DEFAULT_SETTINGS,
  type ExtensionSettings,
  type ExtractedPagePosting,
} from "../shared/types";

// Open Side Panel automatically when user clicks the JobRecall toolbar icon
chrome.sidePanel
  .setPanelBehavior({ openPanelOnActionClick: true })
  .catch(() => {});

const BOARD_HOSTS = [
  "linkedin.com",
  "indeed.com",
  "dice.com",
  "glassdoor.com",
  "ziprecruiter.com",
  "monster.com",
  "simplyhired.com",
  "wellfound.com",
  "builtin.com",
  "jobright.ai",
];
const ATS_HOSTS = [
  "greenhouse.io",
  "myworkdayjobs.com",
  "myworkdaysite.com",
  "lever.co",
  "ashbyhq.com",
  "taleo.net",
  "icims.com",
  "smartrecruiters.com",
  "jobvite.com",
  "workable.com",
  "oraclecloud.com",
];

function isBoardHost(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^www\./, "");
  return BOARD_HOSTS.some((board) => host === board || host.endsWith(`.${board}`));
}

function jobrightHeading(
  title: string | null | undefined
): { title: string; company: string } | null {
  const match = (title || "")
    .replace(/\s+/g, " ")
    .trim()
    .match(/^(.*?)\s+@\s+(.*?)\s*\|\s*Jobright(?:\.ai)?$/i);
  if (!match) return null;
  const company = match[2].trim();
  if (!company || /^jobright(?:\.ai|\s+ai)?$/i.test(company)) return null;
  return { title: match[1].trim(), company };
}

function isJobrightBoardCompany(name: string | null | undefined): boolean {
  const compact = (name || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
  return (
    !compact ||
    compact === "jobright" ||
    compact === "jobrightai" ||
    compact === "unknowncompany" ||
    compact === "company"
  );
}

function recoverJobrightCompany(
  posting: ExtractedPagePosting,
  tabTitle?: string | null
): ExtractedPagePosting {
  let host = "";
  try {
    host = new URL(posting.source_url).hostname.toLowerCase();
  } catch {
    return posting;
  }
  if (!host.includes("jobright.ai") || !isJobrightBoardCompany(posting.company)) {
    return posting;
  }
  const heading = jobrightHeading(tabTitle) || jobrightHeading(posting.title);
  if (!heading) return posting;
  return {
    ...posting,
    platform: posting.platform || "jobright",
    company: heading.company,
    title:
      posting.title && !/^(?:untitled position|job posting)$/i.test(posting.title)
        ? posting.title
        : heading.title,
  };
}

const PLACEHOLDER_TITLE_RE =
  /^(?:thank you|thanks for applying|thank you for applying|application submitted|application received|application confirmation|confirmation|candidate acquisition|career section|careers|apply|apply now|apply online|job application|untitled position|job posting|sign in|log in|login|workday|my workday|taleo|icims|greenhouse)$/i;

function isPlaceholderOrWizardPosting(p: ExtractedPagePosting | null | undefined): boolean {
  if (!p) return true;
  const title = (p.title || "").trim();
  if (!title || PLACEHOLDER_TITLE_RE.test(title)) {
    return true;
  }
  const url = (p.source_url || "").toLowerCase();
  if (
    /\/candidateacquisition\/flow\.jsf|\/apply\/(?:thank-?you|confirmation|submitted)/i.test(
      url
    ) &&
    (!p.description_text || p.description_text.trim().length < 80)
  ) {
    return true;
  }
  return false;
}

export interface RecentBoardPosting {
  posting: ExtractedPagePosting;
  timestamp: number;
}

export async function getSettings(): Promise<ExtensionSettings> {
  const stored = await chrome.storage.local.get("jobrecall_settings");
  return { ...DEFAULT_SETTINGS, ...(stored.jobrecall_settings || {}) };
}

export async function saveSettings(
  partial: Partial<ExtensionSettings>
): Promise<ExtensionSettings> {
  const current = await getSettings();
  const updated = { ...current, ...partial };
  await chrome.storage.local.set({ jobrecall_settings: updated });
  return updated;
}

async function recordBoardPostingIfApplicable(
  posting: ExtractedPagePosting
): Promise<void> {
  try {
    if (isPlaceholderOrWizardPosting(posting)) {
      return;
    }
    const entry: RecentBoardPosting = {
      posting,
      timestamp: Date.now(),
    };
    await chrome.storage.session.set({ last_valid_posting: entry });

    const host = new URL(posting.source_url).hostname.toLowerCase();
    if (BOARD_HOSTS.some((b) => host.includes(b))) {
      await chrome.storage.session.set({ last_board_posting: entry });
    }
  } catch {
    // Ignore invalid URLs
  }
}

async function mergeWithLastValidPostingIfWizard(
  current: ExtractedPagePosting
): Promise<ExtractedPagePosting> {
  if (!isPlaceholderOrWizardPosting(current)) {
    return current;
  }
  try {
    const data = await chrome.storage.session.get([
      "last_valid_posting",
      "last_board_posting",
    ]);
    const recent: RecentBoardPosting | undefined =
      data.last_valid_posting || data.last_board_posting;
    if (!recent || Date.now() - recent.timestamp > 30 * 60 * 1000) {
      return current;
    }
    const prev = recent.posting;
    return {
      ...prev,
      company:
        current.company &&
        current.company.toLowerCase() !== "unknown company" &&
        prev.company.toLowerCase() === "unknown company"
          ? current.company
          : prev.company,
      canonical_ats_url: current.source_url,
    };
  } catch {
    return current;
  }
}

export async function checkRecentBoardRedirect(
  currentPosting: ExtractedPagePosting
): Promise<RecentBoardPosting | null> {
  try {
    const host = new URL(currentPosting.source_url).hostname.toLowerCase();
    const isAts =
      ATS_HOSTS.some((a) => host.includes(a)) ||
      currentPosting.source_url.includes("gh_jid=");
    if (!isAts) return null;

    const data = await chrome.storage.session.get("last_board_posting");
    const recent: RecentBoardPosting | undefined = data.last_board_posting;
    if (!recent) return null;

    // 15-minute TTL
    if (Date.now() - recent.timestamp > 15 * 60 * 1000) {
      return null;
    }
    return recent;
  } catch {
    return null;
  }
}

async function apiFetch(
  path: string,
  options: RequestInit = {},
  timeoutMs = 15500
): Promise<{ ok: boolean; status: number; data: unknown }> {
  const settings = await getSettings();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const headers = new Headers(options.headers || {});
    headers.set("X-JobRecall-Token", settings.apiToken);
    if (!(options.body instanceof FormData) && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }

    const resp = await fetch(
      `${settings.apiBaseUrl.replace(/\/$/, "")}${path}`,
      {
        ...options,
        headers,
        signal: controller.signal,
      }
    );

    const text = await resp.text();
    let data: unknown = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { raw: text };
    }

    return { ok: resp.ok, status: resp.status, data };
  } catch (err: unknown) {
    const isAbort = err instanceof DOMException && err.name === "AbortError";
    return {
      ok: false,
      status: isAbort ? 504 : 0,
      data: {
        detail: {
          code: isAbort ? "AI_TIMEOUT" : "BACKEND_OFFLINE",
          message: isAbort
            ? "Request timed out after 15 seconds."
            : "Cannot reach local JobRecall backend at 127.0.0.1:8765. Ensure Docker container or uvicorn is running.",
        },
      },
    };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Finds the active web tab (or falls back to the last web tab in the current window if user is on chrome://extensions).
 */
async function findTargetWebTab(): Promise<chrome.tabs.Tab | null> {
  const [activeTab] = await chrome.tabs.query({
    active: true,
    currentWindow: true,
  });
  if (
    activeTab?.id &&
    activeTab.url &&
    /^https?:\/\//i.test(activeTab.url)
  ) {
    return activeTab;
  }

  // Fallback: if active tab is chrome://extensions or devtools, find the most recently accessed http/https tab in the window
  const windowTabs = await chrome.tabs.query({ currentWindow: true });
  const webTabs = windowTabs.filter(
    (t) => t.id && t.url && /^https?:\/\//i.test(t.url)
  );
  if (webTabs.length > 0) {
    webTabs.sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0));
    return webTabs[0];
  }
  return null;
}

/**
 * Extracts job details from the target tab using Content Script, dynamic re-injection, and multi-frame scripting fallback.
 */
async function extractFromActiveTab(): Promise<ExtractedPagePosting | null> {
  const tab = await findTargetWebTab();
  if (!tab?.id || !tab.url) {
    return null;
  }

  let extracted: ExtractedPagePosting | null = null;

  // Step 1: Try messaging already-injected content.js
  try {
    const res = await chrome.tabs.sendMessage(tab.id, {
      type: "EXTRACT_JOB_FROM_PAGE",
    });
    if (res?.ok && res.payload) {
      extracted = res.payload;
    }
  } catch {
    // Step 2: Content script was not injected or was invalidated by an extension reload; inject content.js now and retry
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: ["content.js"],
      });
      const retryRes = await chrome.tabs.sendMessage(tab.id, {
        type: "EXTRACT_JOB_FROM_PAGE",
      });
      if (retryRes?.ok && retryRes.payload) {
        extracted = retryRes.payload;
      }
    } catch {
      // Proceed to Step 3 fallback
    }
  }

  // Step 3: If still missing description_text (e.g., inside an embedded ATS iframe), inspect subframes
  if (!extracted || !extracted.description_text?.trim()) {
    try {
      const injectedFrames = await chrome.scripting.executeScript({
        target: { tabId: tab.id, allFrames: true },
        func: () => {
          const clean = (s: string | null | undefined) =>
            (s || "").replace(/\s+/g, " ").trim();
          const readable = (el: Element | null | undefined) => {
            if (!el) return "";
            const htmlEl = el as HTMLElement;
            return clean(
              typeof htmlEl.innerText === "string" && htmlEl.innerText.trim()
                ? htmlEl.innerText
                : el.textContent
            );
          };
          const selection = clean(window.getSelection()?.toString());
          const descContainer =
            document.querySelector("#job-details") ||
            document.querySelector(".jobs-description__content") ||
            document.querySelector("#jobDescriptionText") ||
            document.querySelector(
              '[data-automation-id="jobPostingDescription"]'
            ) ||
            document.querySelector('[itemprop="description"]');
          const bodyText = readable(descContainer).slice(0, 18000);
          const h1 = readable(document.querySelector("h1"));
          const hostName = window.location.hostname.replace(/^www\./, "").toLowerCase();
          const isJobright = hostName === "jobright.ai" || hostName.endsWith(".jobright.ai");
          let jobrightCompany = "";
          let jobrightTitle = "";
          const heading = (document.title || "").match(
            /^(.*?)\s+@\s+(.*?)\s*\|\s*Jobright(?:\.ai)?\s*$/i
          );
          if (heading && !/^jobright(?:\.ai|\s+ai)?$/i.test(heading[2].trim())) {
            jobrightTitle = clean(heading[1]);
            jobrightCompany = clean(heading[2]);
          }
          if (isJobright) {
            try {
              const raw =
                document.getElementById("jobright-helper-job-detail-info")?.textContent || "";
              if (raw) {
                const data = JSON.parse(raw) as {
                  jobResult?: { jobTitle?: string };
                  companyResult?: { companyName?: string };
                };
                const name = clean(data.companyResult?.companyName);
                const jobTitle = clean(data.jobResult?.jobTitle);
                if (name && !/^jobright(?:\.ai|\s+ai)?$/i.test(name)) jobrightCompany = name;
                if (jobTitle) jobrightTitle = jobTitle;
              }
            } catch {
              // Ignore malformed Jobright JSON
            }
          }
          const ogName = clean(
            document.querySelector('meta[property="og:site_name"]')?.getAttribute("content")
          );
          const ogIsBoard = !ogName || /^jobright(?:\.ai|\s+ai)?$/i.test(ogName);
          return {
            source_url: window.location.href,
            company:
              jobrightCompany ||
              (!isJobright && !ogIsBoard ? ogName : "") ||
              (!isJobright
                ? window.location.hostname.replace(/^www\./, "").split(".")[0]
                : "") ||
              "Unknown Company",
            title:
              jobrightTitle ||
              h1 ||
              document.title.split("|")[0]?.split("-")[0]?.trim() ||
              "Untitled Position",
            location: null,
            description_text: selection.length >= 60 ? selection : bodyText,
          };
        },
      });

      const validResults = (injectedFrames || [])
        .map((f) => f.result as ExtractedPagePosting | undefined)
        .filter(
          (r): r is ExtractedPagePosting =>
            Boolean(r && (r.description_text?.length || 0) >= 150)
        );

      if (validResults.length > 0) {
        validResults.sort(
          (a, b) =>
            (b.description_text?.length || 0) -
            (a.description_text?.length || 0)
        );
        const bestFrame = validResults[0];
        if (!extracted) {
          extracted = {
            ...bestFrame,
            source_url: tab.url,
          };
        } else if (
          (bestFrame.description_text?.length || 0) >
          (extracted.description_text?.length || 0)
        ) {
          extracted.description_text = bestFrame.description_text;
        }
      }
    } catch {
      // Ignore scripting error if restricted browser page
    }
  }

  if (extracted) {
    extracted = recoverJobrightCompany(extracted, tab.title);
    await recordBoardPostingIfApplicable(extracted);
    return await mergeWithLastValidPostingIfWizard(extracted);
  }

  const fallbackHost = new URL(tab.url).hostname.replace(/^www\./i, "").toLowerCase();
  const heading = jobrightHeading(tab.title);
  const fallback: ExtractedPagePosting = {
    source_url: tab.url,
    company: heading
      ? heading.company
      : isBoardHost(fallbackHost)
        ? "Unknown Company"
        : fallbackHost.split(".")[0] || "Company",
    title: heading?.title || tab.title?.split("|")[0]?.split("-")[0]?.trim() || "Job Posting",
    location: null,
    description_text: "",
    platform: fallbackHost.includes("jobright.ai") ? "jobright" : null,
  };
  return await mergeWithLastValidPostingIfWizard(
    recoverJobrightCompany(fallback, tab.title)
  );
}

// Central message router for Side Panel and Content Scripts
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    if (msg?.type === "API_REQUEST") {
      const result = await apiFetch(
        msg.path,
        {
          method: msg.method || "GET",
          body: msg.body ? JSON.stringify(msg.body) : undefined,
        },
        msg.timeoutMs || 15500
      );
      sendResponse(result);
      return;
    }

    if (msg?.type === "CAPTURE_ACTIVE_TAB") {
      const payload = await extractFromActiveTab();
      const redirectSource = payload
        ? await checkRecentBoardRedirect(payload)
        : null;
      sendResponse({ ok: true, payload, redirectSource });
      return;
    }

    if (msg?.type === "PAGE_JOB_UPDATED" && msg.payload) {
      await recordBoardPostingIfApplicable(msg.payload);
      const effectivePayload = await mergeWithLastValidPostingIfWizard(
        msg.payload
      );
      // Forward to open Side Panel
      chrome.runtime
        .sendMessage({
          type: "SIDEPANEL_JOB_CHANGED",
          payload: effectivePayload,
        })
        .catch(() => {});
      sendResponse({ ok: true });
      return;
    }

    if (msg?.type === "GET_SETTINGS") {
      sendResponse({ ok: true, settings: await getSettings() });
      return;
    }

    if (msg?.type === "SAVE_SETTINGS") {
      sendResponse({ ok: true, settings: await saveSettings(msg.settings) });
      return;
    }
  })();
  return true;
});

// Notify Side Panel when active tab URL changes or finishes loading
chrome.tabs.onUpdated.addListener((_tabId, changeInfo, tab) => {
  if ((changeInfo.url || changeInfo.status === "complete") && tab.active) {
    chrome.runtime
      .sendMessage({ type: "ACTIVE_TAB_URL_CHANGED", url: tab.url })
      .catch(() => {});
  }
});

chrome.tabs.onActivated.addListener(() => {
  chrome.runtime
    .sendMessage({ type: "ACTIVE_TAB_ACTIVATED" })
    .catch(() => {});
});
