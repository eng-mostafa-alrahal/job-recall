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
    return (val || "")
      .replace(/[\u00b7\u2022\u2023\u25aa\u25cf\ufffd•·▪]+/g, " · ")
      .replace(/\s+/g, " ")
      .trim();
  }

  const JOB_BOARD_DOMAINS = new Set([
    "linkedin",
    "indeed",
    "dice",
    "glassdoor",
    "ziprecruiter",
    "monster",
    "simplyhired",
    "wellfound",
    "builtin",
    "jobright",
  ]);

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
      /^(?:about the job|job description|about this role|about the role)\s*(?:·\s*)?/i,
      ""
    );
    out = out.replace(
      /\s*(?:…\s*more|\.\.\.\s*more|show more|show less|see more|see less)\s*$/i,
      ""
    );
    return cleanText(out);
  }

  function getElementReadableText(el: Element | null | undefined): string {
    if (!el) return "";
    try {
      const clone = el.cloneNode(true) as HTMLElement;
      clone
        .querySelectorAll("script, style, noscript, svg, nav, header, footer")
        .forEach((node) => node.remove());
      // Ensure block/list boundaries have whitespace even on detached DOM clones
      clone
        .querySelectorAll("br, p, div, li, ul, ol, h1, h2, h3, h4, h5, h6, tr, td, section, article")
        .forEach((node) => {
          node.prepend(document.createTextNode(" "));
          node.append(document.createTextNode(" "));
        });
      const raw = clone.textContent || "";
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

  function firstText(selectors: string[], root: ParentNode = document): string {
    for (const sel of selectors) {
      try {
        const els = root.querySelectorAll(sel);
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

  function firstLongestText(
    selectors: string[],
    minLength = 120,
    root: ParentNode = document
  ): string {
    let best = "";
    for (const sel of selectors) {
      try {
        const elements = root.querySelectorAll(sel);
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

  function findHeadingElement(headingRegex: RegExp): HTMLElement | null {
    const candidates = Array.from(
      document.querySelectorAll<HTMLElement>(
        'h1, h2, h3, h4, h5, [role="heading"], span, p, div'
      )
    );
    for (const node of candidates) {
      if (node.children.length > 3) continue;
      const headingText = cleanText(node.textContent);
      if (
        headingText &&
        headingText.length <= 40 &&
        headingRegex.test(headingText)
      ) {
        return node;
      }
    }
    return null;
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
  function extractFromRightSplitPane(minLeftOverride?: number): string {
    const viewportWidth = window.innerWidth || 1000;
    const minLeft =
      minLeftOverride !== undefined
        ? minLeftOverride
        : viewportWidth >= 700
        ? Math.max(240, Math.floor(viewportWidth * 0.3))
        : 0;

    const containers = Array.from(
      document.querySelectorAll("article, section, div")
    );

    let bestScore = 0;
    let bestText = "";

    for (const el of containers) {
      if (el.querySelectorAll("div").length > 140) continue;

      const rect = el.getBoundingClientRect();
      if (minLeft > 0 && rect.left < minLeft) continue;
      if (rect.width < 220 || rect.height < 60) continue;

      const txt = stripLeadingAndTrailingUiNoise(getElementReadableText(el));
      if (txt.length < 180 || txt.length > 22000) continue;

      const lower = txt.toLowerCase();
      if (
        lower.includes("how promoted jobs are ranked") ||
        /^\d+\+?\s+results\b/i.test(txt)
      ) {
        continue;
      }

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

  function isBoardCompanyLabel(raw: string | null | undefined): boolean {
    const txt = cleanText(raw).toLowerCase();
    if (!txt) return true;
    if (JOB_BOARD_DOMAINS.has(txt)) return true;
    const compact = txt.replace(/[^a-z0-9]+/g, "");
    if (JOB_BOARD_DOMAINS.has(compact)) return true;
    // "Jobright.ai", "Jobright AI", and "jobright" are the board, never the employer.
    return compact === "jobright" || compact === "jobrightai";
  }

  function cleanCompanyCandidate(raw: string | null | undefined): string {
    if (!raw) return "";
    let txt = cleanText(raw)
      .replace(/\s+logo$/i, "")
      .replace(/^company\s+name\s*[:·-]?\s*/i, "")
      .trim();
    if (txt.includes("·")) {
      txt = txt.split("·")[0].trim();
    }
    txt = txt.replace(
      /\s+\d+\s+(?:minute|hour|day|week|month|year)s?\s+ago$/i,
      ""
    );
    if (
      !txt ||
      txt.length < 2 ||
      txt.length > 90 ||
      isBoardCompanyLabel(txt) ||
      /^(?:follow|following|see all jobs|view page|about the company|about the job|easy apply|apply|save|saved|promoted|actively reviewing)$/i.test(
        txt
      )
    ) {
      return "";
    }
    return txt;
  }

  /**
   * Dedicated LinkedIn extractor supporting both Classic DOM and 2025/2026 SDUI
   * (/jobs/search-results/, /jobs/collections/, /jobs/view/), independent of scroll position.
   */
  function extractFromLinkedIn(): Partial<ExtractedPagePosting> {
    const url = new URL(window.location.href);
    const isSplitView = !window.location.pathname.startsWith("/jobs/view/");

    // 1. Posting ID. The address bar is the source of truth. Otherwise use the selected card.
    let postingId =
      url.searchParams.get("currentJobId") ||
      window.location.pathname.match(/\/jobs\/view\/(?:[^/]*-)?(\d+)/)?.[1] ||
      null;

    // Locate the active card in the left-hand list (if in split view)
    let activeCard: HTMLElement | null = document.querySelector<HTMLElement>(
      ".jobs-search-results-list__list-item--active, .scaffold-layout__list-item--active, .job-card-container--selected, li[aria-current='true']"
    );
    if (!activeCard && postingId) {
      activeCard =
        document.querySelector<HTMLElement>(
          `[data-occludable-job-id="${postingId}"], [data-job-id="${postingId}"]`
        ) ||
        document
          .querySelector<HTMLElement>(
            `a[href*="/jobs/view/${postingId}"], a[href*="currentJobId=${postingId}"]`
          )
          ?.closest("li, div[data-job-id], .job-card-container") ||
        null;
    }

    if (!postingId && activeCard) {
      const attrId =
        activeCard.getAttribute("data-occludable-job-id") ||
        activeCard.getAttribute("data-job-id");
      if (attrId && /^\d+$/.test(attrId)) {
        postingId = attrId;
      } else {
        const activeLink = activeCard.querySelector<HTMLAnchorElement>(
          'a[href*="/jobs/view/"]'
        );
        const activeMatch = activeLink?.href.match(
          /\/jobs\/view\/(?:[^/?#]*-)?(\d+)/
        );
        if (activeMatch?.[1]) {
          postingId = activeMatch[1];
        }
      }
    }

    // Extract fallback metadata from the active left-pane card
    let cardTitle = "";
    let cardCompany = "";
    let cardLocation = "";
    if (activeCard) {
      cardTitle = firstText(
        [
          ".job-card-list__title--link strong",
          ".job-card-list__title strong",
          ".job-card-container__link strong",
          ".artdeco-entity-lockup__title strong",
          ".artdeco-entity-lockup__title",
          'a[href*="/jobs/"]',
        ],
        activeCard
      );
      cardCompany = cleanCompanyCandidate(
        firstText(
          [
            ".artdeco-entity-lockup__subtitle",
            ".job-card-container__primary-description",
            ".job-card-container__company-name",
          ],
          activeCard
        )
      );
      cardLocation = firstText(
        [
          ".artdeco-entity-lockup__caption",
          ".job-card-container__metadata-item",
        ],
        activeCard
      );
    }

    // Locate the right-hand detail pane container so scroll position never breaks extraction
    let detailPane: HTMLElement | null = document.querySelector<HTMLElement>(
      ".jobs-search__job-details, .jobs-search-two-pane__details, .scaffold-layout__detail, .jobs-details__main-content"
    );
    const aboutJobHeading = findHeadingElement(
      /^(?:about the job|job description|about this role|about the role)$/i
    );
    if (!detailPane && aboutJobHeading) {
      let curr: HTMLElement | null = aboutJobHeading.parentElement;
      while (curr && curr !== document.body) {
        const rect = curr.getBoundingClientRect();
        const textLen = (curr.textContent || "").length;
        if (
          rect.width >= 280 &&
          textLen >= 300 &&
          !(curr.textContent || "")
            .toLowerCase()
            .includes("how promoted jobs are ranked")
        ) {
          detailPane = curr;
        } else if (detailPane) {
          break;
        }
        curr = curr.parentElement;
      }
    }

    const minRightPaneX = detailPane
      ? Math.max(0, detailPane.getBoundingClientRect().left - 30)
      : activeCard
      ? Math.max(240, activeCard.getBoundingClientRect().right - 20)
      : isSplitView && window.innerWidth >= 700
      ? Math.max(260, Math.floor(window.innerWidth * 0.34))
      : 0;

    if (!postingId) {
      const searchRoot = detailPane || document;
      const jobLinks = Array.from(
        searchRoot.querySelectorAll<HTMLAnchorElement>('a[href*="/jobs/view/"]')
      );
      for (const a of jobLinks) {
        if (detailPane || a.getBoundingClientRect().left >= minRightPaneX) {
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

    let titleEl: HTMLElement | null = null;
    if (!title) {
      const searchRoot = detailPane || document;
      const rightCandidates = Array.from(
        searchRoot.querySelectorAll<HTMLElement>(
          'h1, h2, a[href*="/jobs/view/"]'
        )
      );
      for (const el of rightCandidates) {
        const rect = el.getBoundingClientRect();
        if (!detailPane && rect.left < minRightPaneX) {
          continue;
        }
        const txt = cleanText(el.textContent);
        if (
          txt &&
          txt.length >= 3 &&
          txt.length <= 150 &&
          !isSearchHeaderNoise(txt) &&
          !/^(?:about the job|about the company|similar jobs|people also viewed|meet the hiring team|job search|jobs)$/i.test(
            txt
          )
        ) {
          title = txt;
          titleEl = el;
          break;
        }
      }
    }

    if (!title && cardTitle) {
      title = cardTitle;
    }

    // 3. Company
    let company = cleanCompanyCandidate(
      firstText([
        ".job-details-jobs-unified-top-card__company-name a",
        ".job-details-jobs-unified-top-card__company-name",
        ".jobs-unified-top-card__company-name a",
        ".jobs-unified-top-card__company-name",
        "a.topcard__org-name-link",
        ".topcard__org-name-link",
      ])
    );

    // 3b. Check /company/ or /school/ links inside the detail pane (text, aria-label, or logo img alt)
    if (!company) {
      const searchRoot = detailPane || document;
      const companyLinks = Array.from(
        searchRoot.querySelectorAll<HTMLAnchorElement>(
          'a[href*="/company/"], a[href*="/school/"]'
        )
      );
      for (const a of companyLinks) {
        const rect = a.getBoundingClientRect();
        if (!detailPane && rect.left < minRightPaneX) {
          continue;
        }
        const candidate =
          cleanCompanyCandidate(a.textContent) ||
          cleanCompanyCandidate(a.getAttribute("aria-label")) ||
          cleanCompanyCandidate(
            a.querySelector("img")?.getAttribute("alt")
          );
        if (candidate && candidate.toLowerCase() !== title.toLowerCase()) {
          company = candidate;
          break;
        }
      }
    }

    // 3c. In LinkedIn SDUI, check the header container right next to the title element or logo image
    if (!company && titleEl) {
      const headerBox =
        titleEl.closest("header, section, div")?.parentElement ||
        titleEl.parentElement;
      if (headerBox) {
        const imgs = Array.from(headerBox.querySelectorAll<HTMLImageElement>("img[alt]"));
        for (const img of imgs) {
          const altCand = cleanCompanyCandidate(img.getAttribute("alt"));
          if (altCand && altCand.toLowerCase() !== title.toLowerCase()) {
            company = altCand;
            break;
          }
        }
      }
    }

    // 3d. Fallback to active card company in left pane
    if (!company && cardCompany) {
      company = cardCompany;
    }

    // 3e. Fallback to document.title ("Title | Company | LinkedIn" or "Company hiring Title in ...")
    if (!company) {
      const docTitle = cleanText(document.title);
      const hiringMatch = docTitle.match(/^(.+?)\s+hiring\s+/i);
      if (hiringMatch?.[1]) {
        company = cleanCompanyCandidate(hiringMatch[1]);
      } else if (docTitle.includes("|")) {
        const parts = docTitle.split("|").map((s) => s.trim());
        if (parts.length >= 3) {
          company = cleanCompanyCandidate(parts[1]);
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
      const searchRoot = detailPane || document;
      const metaNodes = Array.from(
        searchRoot.querySelectorAll<HTMLElement>("span, p, div")
      );
      for (const node of metaNodes) {
        if (node.children.length > 5) continue;
        const rect = node.getBoundingClientRect();
        if (!detailPane && rect.left < minRightPaneX) {
          continue;
        }
        const txt = cleanText(node.textContent);
        if (
          txt.includes("·") &&
          txt.length < 180 &&
          /(?:reposted|ago|applicant|clicked apply|easy apply|remote|hybrid|on-site|full-time|contract)/i.test(
            txt
          )
        ) {
          const firstPart = cleanText(txt.split("·")[0]);
          if (
            firstPart &&
            firstPart.length >= 2 &&
            firstPart.length <= 65 &&
            firstPart.toLowerCase() !== company.toLowerCase() &&
            firstPart.toLowerCase() !== title.toLowerCase()
          ) {
            location = firstPart;
            break;
          }
        }
      }
    }

    if (!location && cardLocation) {
      location = cardLocation;
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
      description_text = extractFromRightSplitPane(minRightPaneX);
    }

    // 6. Last-resort company extraction from "About the company" section or description sentences
    if (!company && description_text) {
      const patterns = [
        /\bAt\s+([A-Z][A-Za-z0-9&.\- ]{1,35}?),\s+(?:we|our)\b/,
        /\b([A-Z][A-Za-z0-9&.\-]{2,30})\s+is\s+(?:seeking|looking\s+for|hiring|an\s+equal\s+opportunity)/,
        /\b([A-Z][A-Za-z0-9&.\-]{2,30})\s+cannot\s+provide\s+sponsorship/,
      ];
      for (const re of patterns) {
        const m = description_text.match(re);
        if (m?.[1]) {
          const cand = cleanCompanyCandidate(m[1]);
          if (cand) {
            company = cand;
            break;
          }
        }
      }
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

  function stringList(value: unknown): string[] {
    if (!Array.isArray(value)) return [];
    const items: string[] = [];
    for (const item of value) {
      if (typeof item !== "string") continue;
      const text = cleanText(item);
      if (text) items.push(text);
    }
    return items;
  }

  function bulletSection(heading: string, items: string[]): string {
    if (!items.length) return "";
    return `${heading}\n${items.map((item) => `• ${item}`).join("\n")}`;
  }

  function readJobrightPayload(): {
    jobResult?: Record<string, unknown>;
    companyResult?: Record<string, unknown>;
  } | null {
    const helper = document.getElementById("jobright-helper-job-detail-info")?.textContent;
    const nextData = document.getElementById("__NEXT_DATA__")?.textContent;
    const blobs: Array<{ raw: string; nested: boolean }> = [];
    if (helper) blobs.push({ raw: helper, nested: false });
    if (nextData) blobs.push({ raw: nextData, nested: true });

    for (const blob of blobs) {
      try {
        const parsed = JSON.parse(blob.raw) as Record<string, unknown>;
        const source = blob.nested
          ? (
              parsed.props as
                | { pageProps?: { dataSource?: Record<string, unknown> } }
                | undefined
            )?.pageProps?.dataSource
          : parsed;
        if (!source || typeof source !== "object") continue;
        const record = source as Record<string, unknown>;
        if (!record.jobResult && !record.companyResult) continue;
        return {
          jobResult:
            record.jobResult && typeof record.jobResult === "object"
              ? (record.jobResult as Record<string, unknown>)
              : undefined,
          companyResult:
            record.companyResult && typeof record.companyResult === "object"
              ? (record.companyResult as Record<string, unknown>)
              : undefined,
        };
      } catch {
        // Ignore malformed embedded JSON
      }
    }
    return null;
  }

  function jobrightDescription(job: Record<string, unknown>): string {
    const qualifications =
      job.qualifications && typeof job.qualifications === "object"
        ? (job.qualifications as Record<string, unknown>)
        : {};
    return [
      cleanText(typeof job.jobSummary === "string" ? job.jobSummary : ""),
      bulletSection("Responsibilities", stringList(job.coreResponsibilities)),
      bulletSection("Required", stringList(qualifications.mustHave)),
      bulletSection("Preferred", stringList(qualifications.preferredHave)),
      bulletSection("Benefits", stringList(job.benefitsSummaries)),
    ]
      .filter(Boolean)
      .join("\n\n");
  }

  function jobrightLocation(job: Record<string, unknown>): string {
    const places = stringList(job.jobLocations);
    if (places.length) return places.join("; ");
    return cleanText(typeof job.jobLocation === "string" ? job.jobLocation : "");
  }

  function jobrightHeadingFrom(text: string | null | undefined): {
    title: string;
    company: string;
  } {
    const match = cleanText(text).match(
      /^(.*?)\s+@\s+(.*?)\s*\|\s*Jobright(?:\.ai)?$/i
    );
    if (!match) return { title: "", company: "" };
    return {
      title: cleanText(match[1]),
      company: cleanCompanyCandidate(match[2]),
    };
  }

  function firstCleanCompany(selectors: string[]): string {
    for (const sel of selectors) {
      try {
        for (const el of Array.from(document.querySelectorAll(sel))) {
          const name = cleanCompanyCandidate(getElementReadableText(el));
          if (name) return name;
        }
      } catch {
        // Ignore invalid selectors
      }
    }
    return "";
  }

  /**
   * Jobright.ai is a job board. The employer lives in the page JSON and the
   * "Title @ Company | Jobright.ai" heading, not in og:site_name.
   */
  function extractFromJobright(): Partial<ExtractedPagePosting> {
    const urlId =
      window.location.pathname.match(/\/jobs\/info\/([A-Za-z0-9]{16,})/)?.[1] ||
      null;
    const payload = readJobrightPayload();
    const job = payload?.jobResult;
    const jsonJobId = cleanText(typeof job?.jobId === "string" ? job.jobId : "");
    // Only trust embedded JSON when it is the job currently in the address bar.
    // Client-side navigation can leave the previous job's JSON in place.
    const jsonFresh = Boolean(job && jsonJobId && urlId && jsonJobId === urlId);

    const jsonCompany = cleanCompanyCandidate(
      typeof payload?.companyResult?.companyName === "string"
        ? payload.companyResult.companyName
        : ""
    );
    const jsonTitle = cleanText(typeof job?.jobTitle === "string" ? job.jobTitle : "");
    const jsonLocation = job ? jobrightLocation(job) : "";
    const jsonDescription = job && jsonFresh ? jobrightDescription(job) : "";

    const titleEl =
      document.querySelector('[class*="job-title"]') || document.querySelector("h1");
    let domCompany = "";
    if (titleEl) {
      let parent: HTMLElement | null = titleEl.parentElement;
      for (let depth = 0; depth < 5 && parent; depth++) {
        const named = parent.querySelector(
          '[class*="company-name"], [class*="companyName"]'
        );
        domCompany = cleanCompanyCandidate(getElementReadableText(named));
        if (domCompany) break;
        parent = parent.parentElement;
      }
    }
    if (!domCompany) {
      domCompany = firstCleanCompany([
        '[class*="company-name"]',
        '[class*="companyName"]',
      ]);
    }
    const domTitle = firstText(['[class*="job-title"]', "h1"]);
    const domLocationRaw = firstText([
      '[class*="primary-location"]',
      '[class*="job-location"]',
    ]);
    const domLocation = domLocationRaw.length <= 80 ? domLocationRaw : "";

    const titleHeading = jobrightHeadingFrom(document.title);
    const ogHeading = jobrightHeadingFrom(
      document.querySelector('meta[property="og:title"]')?.getAttribute("content")
    );
    const heading = titleHeading.company || titleHeading.title ? titleHeading : ogHeading;

    const metaDesc =
      document.querySelector('meta[name="description"]')?.getAttribute("content") || "";
    const metaMatch = metaDesc.match(/^Apply to (.+?) at (.+?) on Jobright\b/i);
    const metaTitle = cleanText(metaMatch?.[1]);
    const metaCompany = cleanCompanyCandidate(metaMatch?.[2]);

    const company = jsonFresh
      ? jsonCompany || domCompany || heading.company || metaCompany
      : domCompany || heading.company || metaCompany;
    const title = jsonFresh
      ? jsonTitle || domTitle || heading.title || metaTitle
      : domTitle || heading.title || metaTitle;
    const location = jsonFresh ? jsonLocation || domLocation : domLocation;

    const domDescription =
      firstLongestText(
        [
          '[class*="jobDetailContent"]',
          '[class*="sectionContent"]',
          '[class*="jobMainContentInfo"]',
        ],
        80
      ) ||
      extractBySectionHeading(
        /^(?:responsibilities|qualification|qualifications|about the job|job description|about the role)$/i
      );
    const description =
      jsonDescription.length >= 80 ? jsonDescription : domDescription || jsonDescription;

    return {
      platform: "jobright",
      source_posting_id: urlId || (jsonFresh ? jsonJobId : null),
      title: title || undefined,
      company: company || undefined,
      location: location || undefined,
      description_text: description || undefined,
    };
  }

  /**
   * 2. Site-specific DOM adapters for LinkedIn, Indeed, Jobright, Greenhouse, Workday, Lever, Ashby, Dice, Glassdoor, Taleo, iCIMS, SmartRecruiters.
   */
  function extractFromSiteAdapters(): Partial<ExtractedPagePosting> {
    const host = window.location.hostname.toLowerCase();
    const url = new URL(window.location.href);

    if (host.includes("jobright.ai")) {
      return extractFromJobright();
    }

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
      const company = cleanCompanyCandidate(
        firstText([
          '[data-testid="inlineHeader-companyName"]',
          '[data-company-name="true"]',
          ".jobsearch-InlineCompanyRating-companyHeader",
        ])
      );
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

    // Dice
    if (host.includes("dice.com")) {
      const title = firstText([
        '[data-cy="jobTitle"]',
        "h1.jobTitle",
        "h1",
      ]);
      const company = cleanCompanyCandidate(
        firstText([
          '[data-cy="companyNameLink"]',
          '[data-cy="companyNameNoLink"]',
          'a[href*="/company-profile/"]',
        ])
      );
      const location = firstText(['[data-cy="location"]', ".location"]);
      const description_text =
        firstLongestText([
          '[data-testid="jobDescriptionHtml"]',
          "#jobDescription",
          ".job-description",
        ]) || extractBySectionHeading(/^(?:job description|description)$/i);
      return {
        platform: "dice",
        title,
        company,
        location,
        description_text,
      };
    }

    // Glassdoor
    if (host.includes("glassdoor.com")) {
      const title = firstText([
        '[data-test="job-title"]',
        'h1[id*="jd-job-title"]',
        "h1",
      ]);
      const company = cleanCompanyCandidate(
        firstText([
          '[data-test="employer-name"]',
          '[class*="EmployerProfile_employerName"]',
        ])
      );
      const location = firstText(['[data-test="location"]']);
      const description_text =
        firstLongestText([
          '[class*="JobDetails_jobDescription"]',
          ".jobDescriptionContent",
        ]) || extractFromRightSplitPane();
      return {
        platform: "glassdoor",
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

    // Taleo
    if (host.includes("taleo.net")) {
      const title = firstText([
        '[id*="requisitionDescriptionInterface.reqTitleLinkAction"]',
        ".titlepage",
        "h1",
      ]);
      const company = host.split(".")[0] || "";
      const reqId =
        url.searchParams.get("job") ||
        firstText(['[id*="reqContestNumberValue"]']) ||
        null;
      const description_text = firstLongestText([
        '[id*="requisitionDescriptionInterface"]',
        "#taleoContent",
        "main",
      ]);
      return {
        platform: "taleo",
        title,
        company,
        requisition_id: reqId,
        description_text,
      };
    }

    // iCIMS
    if (host.includes("icims.com")) {
      const title = firstText([".iCIMS_Header", "h1"]);
      const company = host.split(".")[0].replace(/^careers-?|^jobs-?/i, "") || "";
      const description_text = firstLongestText([
        ".iCIMS_JobContent",
        ".iCIMS_MainWrapper",
        "main",
      ]);
      return {
        platform: "icims",
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

    const hostStem = window.location.hostname
      .replace(/^www\./i, "")
      .split(".")[0]
      .toLowerCase();
    const isJobBoardHost = JOB_BOARD_DOMAINS.has(hostStem);

    const ogSiteName = cleanCompanyCandidate(
      document
        .querySelector('meta[property="og:site_name"]')
        ?.getAttribute("content")
    );

    const rawCompany =
      adapter.company ||
      jsonLd.company ||
      (!isJobBoardHost ? ogSiteName : "") ||
      (!isJobBoardHost ? hostStem : "") ||
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
