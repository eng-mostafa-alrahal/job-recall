import {
  DEFAULT_SETTINGS,
  type ExtensionSettings,
  type ExtractedPagePosting,
} from "../shared/types";

// Open Side Panel automatically when user clicks the JobRecall toolbar icon
chrome.sidePanel
  .setPanelBehavior({ openPanelOnActionClick: true })
  .catch(() => {});

const BOARD_HOSTS = ["linkedin.com", "indeed.com", "dice.com"];
const ATS_HOSTS = [
  "greenhouse.io",
  "myworkdayjobs.com",
  "myworkdaysite.com",
  "lever.co",
  "ashbyhq.com",
];

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
    const host = new URL(posting.source_url).hostname.toLowerCase();
    if (BOARD_HOSTS.some((b) => host.includes(b))) {
      const entry: RecentBoardPosting = {
        posting,
        timestamp: Date.now(),
      };
      await chrome.storage.session.set({ last_board_posting: entry });
    }
  } catch {
    // Ignore invalid URLs
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

    // 5-minute TTL
    if (Date.now() - recent.timestamp > 5 * 60 * 1000) {
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
          return {
            source_url: window.location.href,
            company:
              clean(
                document
                  .querySelector('meta[property="og:site_name"]')
                  ?.getAttribute("content")
              ) ||
              window.location.hostname.replace(/^www\./, "").split(".")[0] ||
              "Unknown Company",
            title:
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
    await recordBoardPostingIfApplicable(extracted);
    return extracted;
  }

  return {
    source_url: tab.url,
    company:
      new URL(tab.url).hostname.replace(/^www\./, "").split(".")[0] ||
      "Company",
    title: tab.title?.split("|")[0]?.split("-")[0]?.trim() || "Job Posting",
    location: null,
    description_text: "",
  };
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
      // Forward to open Side Panel
      chrome.runtime
        .sendMessage({
          type: "SIDEPANEL_JOB_CHANGED",
          payload: msg.payload,
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
