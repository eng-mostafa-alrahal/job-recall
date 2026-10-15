import type { ExtractedPagePosting } from "../shared/types";

(() => {
  function isExtensionContextValid(): boolean {
    try {
      return Boolean(
        typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id
      );
    } catch {
      return false;
    }
  }

  function cleanText(val: string | null | undefined): string {
    return (val || "").replace(/\s+/g, " ").trim();
  }

  const NOISE_PATTERNS = [
    /^\d+\+?\s+results\b/i,
    /\bhow promoted jobs are ranked\b/i,
    /^skip to main content$/i,
    /^job search$/i,
  ];

  function isSearchHeaderNoise(txt: string): boolean {
    const cleaned = cleanText(txt);
    if (!cleaned) return true;
    if (
      cleaned.length < 160 &&
      NOISE_PATTERNS.some((re) => re.test(cleaned))
    ) {
      return true;
    }
    return false;
  }

  function stripLeadingAndTrailingUiNoise(txt: string): string {
    let out = cleanText(txt);
    out = out.replace(
      /^(?:about the job|job description|about this role|about the role)\s*/i,
      ""
    );
    out = out.replace(/\s*(?:…\s*more|\.\.\.\s*more|show more|show less|see more|see less)\s*$/i, "");
    return cleanText(out);
  }

  function getElementReadableText(el: Element | null | undefined): string {
    if (!el) return "";
    try {
      const clone = el.cloneNode(true) as HTMLElement;
      clone
        .querySelectorAll("script, style, noscript, svg, nav, header, footer")
        .forEach((node) => node.remove());
      const raw =
        typeof clone.innerText === "string" && clone.innerText.trim().length > 0
          ? clone.innerText
          : clone.textContent || "";
      return cleanText(raw);
    } catch {
      const htmlEl = el as HTMLElement;
      const raw =
        typeof htmlEl.innerText === "string" && htmlEl.innerText.trim().length > 0
          ? htmlEl.innerText
          : el.textContent || "";
      return cleanText(raw);
    }
  }

  function stripHtmlTags(html: string): string {
    const doc = new DOMParser().parseFromString(html, "text/html");
    return getElementReadableText(doc.body);
  }

  function firstText(selectors: string[]): string {
    for (const sel of selectors) {
      try {
        const els = document.querySelectorAll(sel);
        for (const el of Array.from(els)) {
          const txt = getElementReadableText(el);
          if (txt && !isSearchHeaderNoise(txt)) {
            return txt;
          }
        }
      } catch {
        // Ignore invalid selector
      }
    }
    return "";
  }

  function firstLongestText(selectors: string[], minLength = 120): string {
    let best = "";
    for (const sel of selectors) {
      try {
        const elements = document.querySelectorAll(sel);
        for (const el of Array.from(elements)) {
          const txt = stripLeadingAndTrailingUiNoise(getElementReadableText(el));
          if (isSearchHeaderNoise(txt)) continue;
          if (txt.length > best.length) {
            best = txt;
          }
        }
        if (best.length >= minLength) {
          return best;
        }
      } catch {
        // Ignore invalid selector
      }
    }
    return best.length >= minLength ? best : "";
  }

  /**
   * Finds a job description section anchored by a visible heading like "About the job" or "Job description".
   * Works even when the site uses obfuscated/hashed CSS class names (e.g. LinkedIn SDUI).
   */
  function extractBySectionHeading(headingRegex: RegExp): string {
    const candidates = Array.from(
      document.querySelectorAll('h1, h2, h3, h4, h5, [role="heading"], span, p, div')
    );

    let bestText = "";

    for (const node of candidates) {
      // Only check leaf-like heading nodes so we don't match huge parent containers
      if (node.children.length > 3) continue;
      const headingText = cleanText(node.textContent);
      if (
        !headingText ||
        headingText.length > 40 ||
        !headingRegex.test(headingText)
      ) {
        continue;
      }

      // 1. Check immediate next sibling
      const nextSib = node.nextElementSibling;
      if (nextSib) {
        const sibText = stripLeadingAndTrailingUiNoise(
          getElementReadableText(nextSib)
        );
        if (
          sibText.length >= 120 &&
          sibText.length < 25000 &&
          !sibText.toLowerCase().includes("how promoted jobs are ranked") &&
          sibText.length > bestText.length
        ) {
          bestText = sibText;
        }
      }

      // 2. Walk up ancestors (up to 6 levels) to find the enclosing "About the job" card/section
      let parent: HTMLElement | null = node.parentElement;
      for (let depth = 0; depth < 6 && parent; depth++) {
        if (parent === document.body || parent.tagName === "HTML") break;
        const parentText = stripLeadingAndTrailingUiNoise(
          getElementReadableText(parent)
        );
        const lower = parentText.toLowerCase();

        // Stop if we walked too high and included the left-hand search results list
        if (
          lower.includes("how promoted jobs are ranked") ||
          /^\d+\+?\s+results\b/i.test(parentText)
        ) {
          break;
        }

        if (parentText.length >= 140 && parentText.length < 25000) {
          if (parentText.length > bestText.length) {
            bestText = parentText;
          }
          break;
        }
        parent = parent.parentElement;
      }
    }

    return bestText;
  }

  /**
   * Heuristic for split-view job boards (LinkedIn /jobs/search-results/, /jobs/collections/, Indeed, etc.):
   * Finds the job description container inside the right-hand detail panel, ignoring the left-hand search list.
   */
  function extractFromRightSplitPane(): string {
    const viewportWidth = window.innerWidth || 1000;
    const minLeft = viewportWidth >= 700 ? 220 : 0;

    const containers = Array.from(
      document.querySelectorAll("article, section, div")
    );

    let bestScore = 0;
    let bestText = "";

    for (const el of containers) {
      // Skip elements with too many nested div children (like the root page wrapper)
      if (el.querySelectorAll("div").length > 120) continue;

      const rect = el.getBoundingClientRect();
      if (minLeft > 0 && rect.left < minLeft) continue;
      if (rect.width < 220 || rect.height < 60) continue;

      const txt = stripLeadingAndTrailingUiNoise(getElementReadableText(el));
      if (txt.length < 180 || txt.length > 20000) continue;

      const lower = txt.toLowerCase();
      if (
        lower.includes("how promoted jobs are ranked") ||
        /^\d+\+?\s+results\b/i.test(txt)
      ) {
        continue;
      }

      // Score by job-description keywords
      let keywordHits = 0;
      const keywords = [
        "experience",
        "qualifications",
        "responsibilities",
        "requirements",
        "skills",
        "education",
        "bachelor",
        "degree",
        "years",
        "role",
        "team",
        "benefits",
        "salary",
        "about the job",
      ];
      for (const kw of keywords) {
        if (lower.includes(kw)) keywordHits++;
      }

      if (keywordHits < 2) continue;

      const score = keywordHits * 400 + Math.min(txt.length, 6000);
      if (score > bestScore) {
        bestScore = score;
        bestText = txt;
      }
    }

    return bestText;
  }

  /**
   * 1. Parse structured Google Jobs `JobPosting` JSON-LD if embedded on the page.
   */
  function extractFromJsonLd(): Partial<ExtractedPagePosting> | null {
    const scripts = Array.from(
      document.querySelectorAll('script[type="application/ld+json"]')
    );
    for (const script of scripts) {
      try {
        const raw = JSON.parse(script.textContent || "{}");
        const items = Array.isArray(raw)
          ? raw
          : raw["@graph"] && Array.isArray(raw["@graph"])
          ? raw["@graph"]
          : [raw];

        for (const item of items) {
          if (item && item["@type"] === "JobPosting") {
            const title = cleanText(item.title);
            const company = cleanText(
              item.hiringOrganization?.name || item.organization?.name
            );
            const reqId = cleanText(
              item.identifier?.value || item.reqId || item.jobId
            );
            const rawDesc =
              typeof item.description === "string"
                ? stripHtmlTags(item.description)
                : "";
            let location = "";
            const locObj = Array.isArray(item.jobLocation)
              ? item.jobLocation[0]
              : item.jobLocation;
            if (locObj?.address) {
              const addr = locObj.address;
              location = [
                addr.addressLocality,
                addr.addressRegion,
                addr.addressCountry,
              ]
                .filter(Boolean)
                .join(", ");
            }
            return {
              title: title || undefined,
              company: company || undefined,
              requisition_id: reqId || undefined,
              location: location || undefined,
              description_text: rawDesc || undefined,
            };
          }
        }
      } catch {
        // Ignore malformed JSON-LD blocks
      }
    }
    return null;
  }

  /**
   * Dedicated LinkedIn extractor supporting both Classic DOM and 2025/2026 SDUI (/jobs/search-results/, /jobs/collections/, /jobs/view/).
   */
  function extractFromLinkedIn(): Partial<ExtractedPagePosting> {
    const url = new URL(window.location.href);
    const isSplitView = !window.location.pathname.startsWith("/jobs/view/");
    const minRightPaneX = isSplitView && window.innerWidth >= 700 ? 220 : 0;

    // 1. Posting ID
    let postingId =
      url.searchParams.get("currentJobId") ||
      window.location.pathname.match(/\/jobs\/view\/(?:[^/]*-)?(\d+)/)?.[1] ||
      null;

    if (!postingId) {
      const jobLinks = Array.from(
        document.querySelectorAll<HTMLAnchorElement>('a[href*="/jobs/view/"]')
      );
      for (const a of jobLinks) {
        if (a.getBoundingClientRect().left >= minRightPaneX) {
          const m = a.href.match(/\/jobs\/view\/(?:[^/?#]*-)?(\d+)/);
          if (m?.[1]) {
            postingId = m[1];
            break;
          }
        }
      }
    }

    // 2. Title
    let title = firstText([
      ".job-details-jobs-unified-top-card__job-title h1",
      ".job-details-jobs-unified-top-card__job-title",
      ".jobs-unified-top-card__job-title h1",
      ".jobs-unified-top-card__job-title",
      "h1.top-card-layout__title",
      ".t-24.job-details-jobs-unified-top-card__job-title",
    ]);

    if (!title) {
      // Check right-pane headings or job view links in the top card
      const rightCandidates = Array.from(
        document.querySelectorAll<HTMLElement>(
          'a[href*="/jobs/view/"], h1, h2'
        )
      );
      for (const el of rightCandidates) {
        const rect = el.getBoundingClientRect();
        if (rect.left < minRightPaneX || rect.top < 0 || rect.top > 450) {
          continue;
        }
        const txt = cleanText(el.textContent);
        if (
          txt &&
          txt.length >= 3 &&
          txt.length <= 140 &&
          !isSearchHeaderNoise(txt) &&
          !/^(about the job|similar jobs|people also viewed|meet the hiring team)$/i.test(
            txt
          )
        ) {
          title = txt;
          break;
        }
      }
    }

    // 3. Company
    let company = firstText([
      ".job-details-jobs-unified-top-card__company-name a",
      ".job-details-jobs-unified-top-card__company-name",
      ".jobs-unified-top-card__company-name a",
      ".jobs-unified-top-card__company-name",
      "a.topcard__org-name-link",
      ".topcard__org-name-link",
    ]);

    if (!company) {
      const companyLinks = Array.from(
        document.querySelectorAll<HTMLAnchorElement>(
          'a[href*="/company/"], a[href*="/school/"]'
        )
      );
      for (const a of companyLinks) {
        const rect = a.getBoundingClientRect();
        if (rect.left < minRightPaneX || rect.top < 0 || rect.top > 420) {
          continue;
        }
        const txt = cleanText(a.textContent);
        if (txt && txt.length >= 2 && txt.length <= 100) {
          company = txt;
          break;
        }
      }
    }

    // 4. Location
    let location = firstText([
      ".job-details-jobs-unified-top-card__primary-description-container .tvm__text",
      ".jobs-unified-top-card__bullet",
      ".topcard__flavor--bullet",
    ]);

    if (!location) {
      const metaNodes = Array.from(
        document.querySelectorAll<HTMLElement>("span, p, div")
      );
      for (const node of metaNodes) {
        if (node.children.length > 4) continue;
        const rect = node.getBoundingClientRect();
        if (rect.left < minRightPaneX || rect.top < 60 || rect.top > 450) {
          continue;
        }
        const txt = cleanText(node.textContent);
        if (
          txt.includes("·") &&
          txt.length < 160 &&
          /(?:reposted|ago|applicant|clicked apply|easy apply|remote|hybrid|on-site)/i.test(
            txt
          )
        ) {
          const firstPart = cleanText(txt.split("·")[0]);
          if (
            firstPart &&
            firstPart.length >= 2 &&
            firstPart.length <= 65 &&
            firstPart.toLowerCase() !== company.toLowerCase()
          ) {
            location = firstPart;
            break;
          }
        }
      }
    }

    // 5. Job Description (Classic selectors -> "About the job" heading section -> Right split pane)
    let description_text = firstLongestText(
      [
        "#job-details",
        ".jobs-description__content",
        ".jobs-description-content__text",
        ".jobs-box__html-content",
        ".show-more-less-html__markup",
        "article.jobs-description__container",
        "[class*='jobs-description']",
      ],
      120
    );

    if (!description_text) {
      description_text = extractBySectionHeading(
        /^(?:about the job|job description|about this role|about the role)$/i
      );
    }

    if (!description_text) {
      description_text = extractFromRightSplitPane();
    }

    return {
      platform: "linkedin",
      source_posting_id: postingId,
      title,
      company,
      location,
      description_text,
    };
  }

  /**
   * 2. Site-specific DOM adapters for LinkedIn, Indeed, Greenhouse, Workday, Lever, Ashby, Dice.
   */
  function extractFromSiteAdapters(): Partial<ExtractedPagePosting> {
    const host = window.location.hostname.toLowerCase();
    const url = new URL(window.location.href);

    if (host.includes("linkedin.com")) {
      return extractFromLinkedIn();
    }

    // Indeed (Split-view ?vjk=... and /viewjob?jk=...)
    if (host.includes("indeed.com")) {
      const title = firstText([
        '[data-testid="jobsearch-JobInfoHeader-title"]',
        ".jobsearch-JobInfoHeader-title",
        "h1",
      ]);
      const company = firstText([
        '[data-testid="inlineHeader-companyName"]',
        '[data-company-name="true"]',
        ".jobsearch-InlineCompanyRating-companyHeader",
      ]);
      const location = firstText([
        '[data-testid="inlineHeader-companyLocation"]',
        '[data-testid="job-location"]',
      ]);
      const description_text =
        firstLongestText([
          "#jobDescriptionText",
          '[data-testid="jobDescriptionText"]',
          ".jobsearch-JobComponent-description",
          ".jobsearch-jobDescriptionText",
        ]) ||
        extractBySectionHeading(/^(?:full job description|job description)$/i) ||
        extractFromRightSplitPane();
      const postingId =
        url.searchParams.get("vjk") || url.searchParams.get("jk") || null;

      return {
        platform: "indeed",
        source_posting_id: postingId,
        title,
        company,
        location,
        description_text,
      };
    }

    // Greenhouse
    if (host.includes("greenhouse.io") || url.searchParams.has("gh_jid")) {
      const title = firstText([".app-title", "h1.section-header", "h1"]);
      const company =
        firstText([".company-name"]) ||
        window.location.pathname.split("/").filter(Boolean)[0] ||
        "";
      const location = firstText([".location", ".job__location"]);
      const description_text = firstLongestText([
        "#content",
        "#app_body",
        ".job__description",
        "main",
      ]);
      return {
        platform: "greenhouse",
        title,
        company,
        location,
        description_text,
      };
    }

    // Workday
    if (
      host.includes("myworkdayjobs.com") ||
      host.includes("myworkdaysite.com")
    ) {
      const title = firstText([
        '[data-automation-id="jobPostingHeader"]',
        "h2",
        "h1",
      ]);
      const company = host.split(".")[0] || "";
      const location = firstText(['[data-automation-id="locations"]']);
      const reqId = firstText(['[data-automation-id="requisitionId"]']);
      const description_text = firstLongestText([
        '[data-automation-id="jobPostingDescription"]',
        '[data-automation-id="job-posting-details"]',
        "main",
      ]);
      return {
        platform: "workday",
        title,
        company,
        location,
        requisition_id: reqId || null,
        description_text,
      };
    }

    // Lever
    if (host.includes("lever.co")) {
      const title = firstText([".posting-headline h2", "h1", "h2"]);
      const company =
        window.location.pathname.split("/").filter(Boolean)[0] ||
        document.title.split("-")[0] ||
        "";
      const location = firstText([".posting-categories .location"]);
      const description_text = firstLongestText([
        ".section-wrapper.page-full-width",
        ".content",
        "main",
      ]);
      return {
        platform: "lever",
        title,
        company,
        location,
        description_text,
      };
    }

    // Ashby
    if (host.includes("ashbyhq.com")) {
      const title = firstText(["h1"]);
      const company =
        window.location.pathname.split("/").filter(Boolean)[0] || "";
      const description_text = firstLongestText(["main", "body"]);
      return {
        platform: "ashby",
        title,
        company,
        description_text,
      };
    }

    // Generic semantic job description containers on custom career portals
    const genericDesc =
      firstLongestText([
        '[itemprop="description"]',
        '[data-testid*="job-description" i]',
        '[data-testid*="jobDescription" i]',
        '[id*="job-description" i]',
        '[id*="jobDescription" i]',
        '[class*="job-description" i]',
        '[class*="jobDescription" i]',
      ]) ||
      extractBySectionHeading(
        /^(?:about the job|job description|about this role|about the role|role overview|what you'll do|responsibilities)$/i
      ) ||
      extractFromRightSplitPane();

    return {
      description_text: genericDesc || undefined,
    };
  }

  function extractCurrentPageJob(): ExtractedPagePosting {
    const jsonLd = extractFromJsonLd() || {};
    const adapter = extractFromSiteAdapters();

    const selectedText = cleanText(window.getSelection()?.toString() || "");

    // Fallback: pick the longest non-noise main/article container rather than blindly picking the first <main>
    const fallbackBodyText = firstLongestText(
      ["article", "main", '[role="main"]'],
      200
    ).slice(0, 18000);

    const rawTitle =
      adapter.title ||
      jsonLd.title ||
      firstText(["h1", "h2"]) ||
      document.title.split("|")[0]?.split("-")[0]?.trim() ||
      "Untitled Position";

    const rawCompany =
      adapter.company ||
      jsonLd.company ||
      document
        .querySelector('meta[property="og:site_name"]')
        ?.getAttribute("content") ||
      window.location.hostname.replace(/^www\./, "").split(".")[0] ||
      "Unknown Company";

    const description =
      selectedText.length >= 60
        ? selectedText
        : adapter.description_text ||
          jsonLd.description_text ||
          fallbackBodyText;

    return {
      source_url: window.location.href,
      platform: adapter.platform || null,
      source_posting_id: adapter.source_posting_id || null,
      requisition_id: adapter.requisition_id || jsonLd.requisition_id || null,
      company: cleanText(rawCompany),
      title: cleanText(rawTitle),
      location: cleanText(adapter.location || jsonLd.location || "") || null,
      description_text: description,
    };
  }

  if (!isExtensionContextValid()) {
    return;
  }

  const runtimeId = chrome.runtime.id;
  const globalWin = window as unknown as {
    __JOBRECALL_ACTIVE_RUNTIME_ID__?: string;
  };

  if (globalWin.__JOBRECALL_ACTIVE_RUNTIME_ID__ === runtimeId) {
    return;
  }
  globalWin.__JOBRECALL_ACTIVE_RUNTIME_ID__ = runtimeId;

  // Listen for explicit extraction requests from Background / Side Panel
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!isExtensionContextValid()) {
      return false;
    }
    if (msg?.type === "EXTRACT_JOB_FROM_PAGE") {
      sendResponse({ ok: true, payload: extractCurrentPageJob() });
      return true;
    }
    return false;
  });

  // Observe SPA URL / job-detail mutations (e.g., clicking a new job card in LinkedIn or Indeed split-view)
  let lastNotifiedKey = "";
  let debounceTimer: number | undefined;
  let observer: MutationObserver | null = null;

  function notifyIfJobChanged(): void {
    if (!isExtensionContextValid()) {
      if (observer) {
        observer.disconnect();
        observer = null;
      }
      return;
    }

    const extracted = extractCurrentPageJob();
    const descLenBucket = Math.floor(
      (extracted.description_text || "").length / 200
    );
    const key = `${extracted.source_url}|${
      extracted.source_posting_id || ""
    }|${extracted.title}|${extracted.company}|${descLenBucket}`;

    if (key !== lastNotifiedKey && extracted.title && extracted.company) {
      lastNotifiedKey = key;
      try {
        chrome.runtime
          .sendMessage({ type: "PAGE_JOB_UPDATED", payload: extracted })
          .catch(() => {});
      } catch {
        if (observer) {
          observer.disconnect();
          observer = null;
        }
      }
    }
  }

  if (document.body) {
    observer = new MutationObserver(() => {
      if (!isExtensionContextValid()) {
        if (observer) {
          observer.disconnect();
          observer = null;
        }
        return;
      }
      if (debounceTimer) window.clearTimeout(debounceTimer);
      debounceTimer = window.setTimeout(notifyIfJobChanged, 550);
    });

    observer.observe(document.body, {
      childList: true,
      subtree: true,
    });
  }
})();
