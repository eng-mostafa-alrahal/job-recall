import hashlib
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

TRACKING_QUERY_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "trk",
    "trkinfo",
    "refid",
    "trackingid",
    "lipi",
    "gh_src",
    "source",
    "ref",
    "fbclid",
    "gclid",
    "from",
    "tk",
    "advn",
}

COMPANY_SUFFIXES = {
    "inc",
    "incorporated",
    "llc",
    "ltd",
    "limited",
    "corp",
    "corporation",
    "co",
    "company",
    "group",
    "holdings",
    "technologies",
    "technology",
    "tech",
    "labs",
    "systems",
    "solutions",
    "plc",
    "gmbh",
}

TITLE_SYNONYMS = {
    "sr": "senior",
    "jr": "junior",
    "swe": "software engineer",
    "sde": "software development engineer",
    "fe": "frontend",
    "be": "backend",
    "fullstack": "full stack",
    "ml": "machine learning",
    "ai": "artificial intelligence",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "mgr": "manager",
    "eng": "engineer",
    "dev": "developer",
}

BOILERPLATE_PATTERNS = [
    r"\breposted\s+\d+\s+\w+\s+ago\b",
    r"\bposted\s+\d+\s+\w+\s+ago\b",
    r"\bover\s+\d+\s+applicants\b",
    r"\b\d+\s+applicants\b",
    r"\bactively\s+reviewing\s+applicants\b",
    r"\bshow\s+more\b",
    r"\bshow\s+less\b",
    r"\bpromoted\s+by\s+hirer\b",
    r"\beasy\s+apply\b",
]


@dataclass
class NormalizedPostingMetadata:
    platform: str
    source_url_norm: str
    source_posting_id: str | None
    requisition_id: str | None
    canonical_ats_url: str | None
    company_norm: str
    title_norm: str
    location_norm: str | None
    work_mode: str | None
    cleaned_text: str | None
    cleaned_text_hash: str | None


def normalize_url_and_extract_ids(
    raw_url: str,
    explicit_platform: str | None = None,
    explicit_posting_id: str | None = None,
    explicit_req_id: str | None = None,
    explicit_ats_url: str | None = None,
) -> tuple[str, str, str | None, str | None, str | None]:
    """
    Returns (platform, source_url_norm, source_posting_id, requisition_id, canonical_ats_url).
    Handles SPA query params (LinkedIn currentJobId, Indeed vjk/jk, Greenhouse gh_jid, Workday _JR...).
    """
    url_str = (raw_url or "").strip()
    if not url_str:
        return (
            explicit_platform or "manual",
            "manual://local",
            explicit_posting_id,
            explicit_req_id,
            explicit_ats_url,
        )

    parsed = urlparse(url_str)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host_clean = host[4:]
    else:
        host_clean = host
    path = parsed.path or ""
    query = parse_qs(parsed.query, keep_blank_values=False)

    platform = (explicit_platform or "").lower().strip() or None
    posting_id = (explicit_posting_id or "").strip() or None
    req_id = (explicit_req_id or "").strip() or None
    ats_url = (explicit_ats_url or "").strip() or None

    # 1. LinkedIn
    if "linkedin.com" in host_clean:
        platform = platform or "linkedin"
        m = re.search(r"/jobs/view/(?:[^/?#]*-)?(\d+)", path)
        if m:
            posting_id = posting_id or m.group(1)
        elif "currentJobId" in query and query["currentJobId"]:
            posting_id = posting_id or query["currentJobId"][0]
        if posting_id:
            norm_url = f"https://www.linkedin.com/jobs/view/{posting_id}"
            return platform, norm_url, posting_id, req_id, ats_url

    # 2. Indeed
    elif "indeed.com" in host_clean:
        platform = platform or "indeed"
        jk = (query.get("jk") or query.get("vjk") or [None])[0]
        if jk:
            posting_id = posting_id or jk
        if posting_id:
            norm_url = f"https://www.indeed.com/viewjob?jk={posting_id}"
            return platform, norm_url, posting_id, req_id, ats_url

    # 3. Dice
    elif "dice.com" in host_clean:
        platform = platform or "dice"
        m = re.search(r"/job-detail/([a-zA-Z0-9-]+)", path)
        if m:
            posting_id = posting_id or m.group(1)
            norm_url = f"https://www.dice.com/job-detail/{posting_id}"
            return platform, norm_url, posting_id, req_id, ats_url

    # 4. Greenhouse
    elif "greenhouse.io" in host_clean or "gh_jid" in query:
        platform = platform or "greenhouse"
        m = re.search(r"/([^/]+)/jobs/(\d+)", path)
        if m:
            board_slug, job_num = m.group(1).lower(), m.group(2)
            posting_id = posting_id or f"{board_slug}:{job_num}"
            req_id = req_id or job_num
            norm_url = f"https://boards.greenhouse.io/{board_slug}/jobs/{job_num}"
            ats_url = ats_url or norm_url
            return platform, norm_url, posting_id, req_id, ats_url
        elif "gh_jid" in query and query["gh_jid"]:
            gh_id = query["gh_jid"][0]
            posting_id = posting_id or gh_id
            req_id = req_id or gh_id
            norm_url = f"https://{host}{path.rstrip('/')}?gh_jid={gh_id}"
            ats_url = ats_url or norm_url
            return platform, norm_url, posting_id, req_id, ats_url

    # 5. Workday
    elif "myworkdayjobs.com" in host_clean or "myworkdaysite.com" in host_clean:
        platform = platform or "workday"
        tenant = host_clean.split(".")[0]
        req_match = re.search(r"(?:_|-)((?:JR|R|REQ)[-_]?\d+|[A-Z]{1,4}\d{4,})$", path, re.IGNORECASE)
        if req_match:
            extracted_req = req_match.group(1).upper()
            req_id = req_id or extracted_req
            posting_id = posting_id or f"{tenant}:{extracted_req}"
        else:
            job_slug_match = re.search(r"/job/[^/]+/([^/?#]+)", path)
            if job_slug_match:
                posting_id = posting_id or f"{tenant}:{job_slug_match.group(1)}"
        norm_url = f"https://{host}{path.rstrip('/')}"
        ats_url = ats_url or norm_url
        return platform, norm_url, posting_id, req_id, ats_url

    # 6. Lever
    elif "lever.co" in host_clean:
        platform = platform or "lever"
        m = re.search(r"/([^/]+)/([0-9a-fA-F-]{20,})", path)
        if m:
            company_slug, uuid_part = m.group(1).lower(), m.group(2).lower()
            posting_id = posting_id or f"{company_slug}:{uuid_part}"
            norm_url = f"https://jobs.lever.co/{company_slug}/{uuid_part}"
            ats_url = ats_url or norm_url
            return platform, norm_url, posting_id, req_id, ats_url

    # 7. Ashby
    elif "ashbyhq.com" in host_clean:
        platform = platform or "ashby"
        m = re.search(r"/([^/]+)/([0-9a-fA-F-]{20,})", path)
        if m:
            company_slug, uuid_part = m.group(1).lower(), m.group(2).lower()
            posting_id = posting_id or f"{company_slug}:{uuid_part}"
            norm_url = f"https://jobs.ashbyhq.com/{company_slug}/{uuid_part}"
            ats_url = ats_url or norm_url
            return platform, norm_url, posting_id, req_id, ats_url

    # Generic fallback normalization
    platform = platform or "other"
    filtered_query = {
        k: v for k, v in sorted(query.items()) if k.lower() not in TRACKING_QUERY_PARAMS
    }
    clean_query_str = urlencode(filtered_query, doseq=True)
    clean_path = path.rstrip("/") or "/"
    norm_url = urlunparse(
        (parsed.scheme.lower() or "https", host, clean_path, "", clean_query_str, "")
    )
    return platform, norm_url, posting_id, req_id, ats_url


def normalize_company_name(raw_company: str) -> str:
    text = (raw_company or "").lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = [t for t in text.split() if t]
    while len(tokens) > 1 and tokens[-1] in COMPANY_SUFFIXES:
        tokens.pop()
    return " ".join(tokens) if tokens else (raw_company or "").lower().strip()


def normalize_job_title(raw_title: str) -> str:
    text = (raw_title or "").lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = []
    for tok in text.split():
        mapped = TITLE_SYNONYMS.get(tok, tok)
        tokens.extend(mapped.split())
    return " ".join(tokens)


def normalize_location_and_mode(
    raw_location: str | None, explicit_mode: str | None = None
) -> tuple[str | None, str | None]:
    work_mode = (explicit_mode or "").lower().strip() or None
    if not raw_location or not raw_location.strip():
        return None, work_mode

    loc_lower = raw_location.lower().strip()
    if not work_mode:
        if "remote" in loc_lower:
            work_mode = "remote"
        elif "hybrid" in loc_lower:
            work_mode = "hybrid"
        elif "on-site" in loc_lower or "onsite" in loc_lower or "in-office" in loc_lower:
            work_mode = "onsite"

    # Strip parenthetical mode tags like "(Hybrid)" or "(Remote)"
    cleaned = re.sub(r"\((?:remote|hybrid|on-?site|in-?office)\)", " ", loc_lower)
    cleaned = re.sub(r"\b(?:remote|hybrid|on-?site|in-?office)\b", " ", cleaned)
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return (cleaned or loc_lower), work_mode


def clean_description_and_hash(raw_description: str | None) -> tuple[str | None, str | None]:
    if not raw_description or not raw_description.strip():
        return None, None

    text = raw_description.lower()
    for pat in BOILERPLATE_PATTERNS:
        text = re.sub(pat, " ", text, flags=re.IGNORECASE)

    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None, None

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text, digest


def extract_req_id_from_text(description_text: str | None) -> str | None:
    if not description_text:
        return None
    patterns = [
        r"(?:requisition|req|job)\s*(?:id|number|#)\s*[:#-]?\s*([A-Z0-9][-A-Z0-9_]{2,25})",
        r"\b((?:JR|REQ)-\d{3,10})\b",
    ]
    for pat in patterns:
        m = re.search(pat, description_text, flags=re.IGNORECASE)
        if m:
            candidate = m.group(1).strip().upper()
            if any(ch.isdigit() for ch in candidate):
                return candidate
    return None


def normalize_posting_input(
    source_url: str,
    company: str,
    title: str,
    location: str | None = None,
    work_mode: str | None = None,
    platform: str | None = None,
    source_posting_id: str | None = None,
    requisition_id: str | None = None,
    canonical_ats_url: str | None = None,
    description_text: str | None = None,
    **_extra: object,
) -> NormalizedPostingMetadata:
    norm_platform, norm_url, norm_posting_id, norm_req_id, norm_ats_url = (
        normalize_url_and_extract_ids(
            raw_url=source_url,
            explicit_platform=platform,
            explicit_posting_id=source_posting_id,
            explicit_req_id=requisition_id,
            explicit_ats_url=canonical_ats_url,
        )
    )
    if not norm_req_id and description_text:
        norm_req_id = extract_req_id_from_text(description_text)

    company_norm = normalize_company_name(company)
    title_norm = normalize_job_title(title)
    location_norm, detected_mode = normalize_location_and_mode(location, work_mode)
    cleaned_text, cleaned_hash = clean_description_and_hash(description_text)

    return NormalizedPostingMetadata(
        platform=norm_platform,
        source_url_norm=norm_url,
        source_posting_id=norm_posting_id,
        requisition_id=norm_req_id,
        canonical_ats_url=norm_ats_url,
        company_norm=company_norm,
        title_norm=title_norm,
        location_norm=location_norm,
        work_mode=detected_mode,
        cleaned_text=cleaned_text,
        cleaned_text_hash=cleaned_hash,
    )
