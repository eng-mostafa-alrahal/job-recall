import hashlib
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
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
    "ebp",
    "geoid",
    "distance",
    "f_sal",
    "keywords",
    "origin",
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
    "services",
    "consulting",
    "plc",
    "gmbh",
    "llp",
    "lp",
    "international",
    "global",
    "worldwide",
    "americas",
    "us",
    "usa",
    "enterprises",
}

COMPANY_ALIASES = {
    "jpmorgan chase": "jpmorgan",
    "jp morgan chase": "jpmorgan",
    "jp morgan": "jpmorgan",
    "j p morgan": "jpmorgan",
    "jpmorganchase": "jpmorgan",
    "tata consultancy": "tcs",
    "hewlett packard enterprise": "hpe",
    "international business machines": "ibm",
    "pricewaterhousecoopers": "pwc",
    "ernst young": "ey",
    "ernst and young": "ey",
    "meta platforms": "meta",
    "amazon web": "amazon",
    "aws": "amazon",
}

GENERIC_COMPANY_NAMES = {
    "",
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
    "jobright ai",
    "unknown",
    "unknown company",
    "company",
    "web",
    "other",
    "manual",
}

PLACEHOLDER_TITLES = {
    "",
    "thank you",
    "thanks for applying",
    "thank you for applying",
    "application submitted",
    "application received",
    "application confirmation",
    "confirmation",
    "candidate acquisition",
    "career section",
    "careers",
    "apply",
    "apply now",
    "apply online",
    "job application",
    "untitled position",
    "job posting",
    "sign in",
    "log in",
    "login",
    "workday",
    "my workday",
    "taleo",
    "icims",
    "greenhouse",
}

TITLE_SYNONYMS = {
    "sr": "senior",
    "snr": "senior",
    "jr": "junior",
    "jnr": "junior",
    "swe": "software engineer",
    "sde": "software development engineer",
    "fe": "frontend",
    "be": "backend",
    "fullstack": "full stack",
    "genai": "gen artificial intelligence",
    "generative": "gen",
    "ml": "machine learning",
    "ai": "artificial intelligence",
    "llm": "large language model",
    "llms": "large language model",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "mgr": "manager",
    "eng": "engineer",
    "engr": "engineer",
    "dev": "developer",
    "sci": "scientist",
    "princ": "principal",
    "assoc": "associate",
}

CORE_TITLE_NOISE_TOKENS = {
    "full",
    "time",
    "fulltime",
    "part",
    "parttime",
    "only",
    "remote",
    "hybrid",
    "onsite",
    "office",
    "contract",
    "contractor",
    "w2",
    "c2c",
    "1099",
    "permanent",
    "relocation",
    "multiple",
    "locations",
    "location",
    "usa",
    "us",
    "united",
    "states",
    "north",
    "america",
    "or",
    "and",
    "in",
    "at",
    "the",
    "for",
    "with",
    "on",
    "to",
}

US_STATE_CODES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga",
    "hi", "id", "il", "ind", "ia", "ks", "ky", "la", "me", "md",
    "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj",
    "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc",
    "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy",
    "dc", "dfw", "nyc", "sf", "la",
}

BOILERPLATE_PATTERNS = [
    r"\breposted\s+\d+\s+\w+\s+ago\b",
    r"\bposted\s+\d+\s+\w+\s+ago\b",
    r"\bover\s+\d+\s+applicants\b",
    r"\b\d+\s+applicants\b",
    r"\bactively\s+reviewing\s+applicants\b",
    r"\bshow\s+more\b",
    r"\bshow\s+less\b",
    r"\bsee\s+more\b",
    r"\bsee\s+less\b",
    r"\bpromoted\s+by\s+hirer\b",
    r"\bresponses\s+managed\s+off\s+linkedin\b",
    r"\bmatches\s+your\s+job\s+preferences\b",
    r"\beasy\s+apply\b",
    r"\babout\s+the\s+job\b",
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


def is_generic_ats_flow_url(url: str | None) -> bool:
    """Returns True if the URL is a shared ATS wizard/login/confirmation page without a unique job ID."""
    if not url:
        return True
    lower = url.lower().strip()
    if lower in ("manual://local", ""):
        return True
    generic_flow_patterns = [
        r"/candidateacquisition/flow\.jsf$",
        r"/application\.jss$",
        r"/apply/(?:thank-?you|confirmation|complete|submitted)/?$",
        r"/(?:thank-?you|confirmation|application-submitted)/?$",
        r"/(?:login|signin|sign-in|auth)/?$",
    ]
    return any(re.search(pat, lower) for pat in generic_flow_patterns)


def is_generic_company_name(company_norm: str | None) -> bool:
    if not company_norm:
        return True
    cleaned = company_norm.strip().lower()
    return cleaned in GENERIC_COMPANY_NAMES


def is_placeholder_or_confirmation_title(title: str | None) -> bool:
    if not title:
        return True
    cleaned = re.sub(r"[^\w\s]", " ", title.lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned in PLACEHOLDER_TITLES:
        return True
    if any(
         cleaned.startswith(prefix)
        for prefix in (
            "thank you for ",
            "thanks for applying",
            "application submitted",
            "application confirmation",
            "candidate acquisition",
        )
    ):
        return True
    return False


def normalize_url_and_extract_ids(
    raw_url: str,
    explicit_platform: str | None = None,
    explicit_posting_id: str | None = None,
    explicit_req_id: str | None = None,
    explicit_ats_url: str | None = None,
) -> tuple[str, str, str | None, str | None, str | None]:
    """
    Returns (platform, source_url_norm, source_posting_id, requisition_id, canonical_ats_url).
    Handles SPA query params (LinkedIn currentJobId, Indeed vjk/jk, Greenhouse gh_jid, Workday _JR..., Taleo, iCIMS, Glassdoor).
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

    # 1. LinkedIn. The id in the address bar wins over a stale id scraped from the page.
    if "linkedin.com" in host_clean:
        platform = platform or "linkedin"
        m = re.search(r"/jobs/view/(?:[^/?#]*-)?(\d+)", path)
        url_id = m.group(1) if m else None
        if not url_id and query.get("currentJobId"):
            url_id = query["currentJobId"][0]
        if url_id:
            posting_id = url_id
        if posting_id:
            norm_url = f"https://www.linkedin.com/jobs/view/{posting_id}"
            return platform, norm_url, posting_id, req_id, ats_url

    # 2. Indeed. jk/vjk in the address bar wins over a stale scraped id.
    elif "indeed.com" in host_clean:
        platform = platform or "indeed"
        jk = (query.get("jk") or query.get("vjk") or [None])[0]
        if jk:
            posting_id = jk
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

    # 4. Glassdoor
    elif "glassdoor.com" in host_clean:
        platform = platform or "glassdoor"
        jl_id = (query.get("jobListingId") or query.get("jl") or [None])[0]
        if not jl_id:
            m = re.search(r"(?:JL_|_JL)(\d+)", path)
            if m:
                jl_id = m.group(1)
        if jl_id:
            posting_id = posting_id or jl_id
            norm_url = f"https://www.glassdoor.com/job-listing/JL_{posting_id}.htm"
            return platform, norm_url, posting_id, req_id, ats_url

    # 5. Greenhouse
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

    # 6. Workday
    elif "myworkdayjobs.com" in host_clean or "myworkdaysite.com" in host_clean:
        platform = platform or "workday"
        tenant = host_clean.split(".")[0]
        req_match = re.search(
            r"(?:_|-)((?:JR|R|REQ)[-_]?\d+|[A-Z]{1,4}\d{4,})$", path, re.IGNORECASE
        )
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

    # 7. Lever
    elif "lever.co" in host_clean:
        platform = platform or "lever"
        m = re.search(r"/([^/]+)/([0-9a-fA-F-]{20,})", path)
        if m:
            company_slug, uuid_part = m.group(1).lower(), m.group(2).lower()
            posting_id = posting_id or f"{company_slug}:{uuid_part}"
            norm_url = f"https://jobs.lever.co/{company_slug}/{uuid_part}"
            ats_url = ats_url or norm_url
            return platform, norm_url, posting_id, req_id, ats_url

    # 8. Ashby
    elif "ashbyhq.com" in host_clean:
        platform = platform or "ashby"
        m = re.search(r"/([^/]+)/([0-9a-fA-F-]{20,})", path)
        if m:
            company_slug, uuid_part = m.group(1).lower(), m.group(2).lower()
            posting_id = posting_id or f"{company_slug}:{uuid_part}"
            norm_url = f"https://jobs.ashbyhq.com/{company_slug}/{uuid_part}"
            ats_url = ats_url or norm_url
            return platform, norm_url, posting_id, req_id, ats_url

    # 9. Taleo
    elif "taleo.net" in host_clean:
        platform = platform or "taleo"
        tenant = host_clean.split(".")[0]
        job_param = (query.get("job") or query.get("requisitionno") or [None])[0]
        if job_param:
            req_id = req_id or job_param.upper()
            posting_id = posting_id or f"{tenant}:{job_param}"
            norm_url = f"https://{host}{path.rstrip('/')}?job={job_param}"
            ats_url = ats_url or norm_url
            return platform, norm_url, posting_id, req_id, ats_url

    # 10. iCIMS
    elif "icims.com" in host_clean:
        platform = platform or "icims"
        tenant = host_clean.split(".")[0]
        m = re.search(r"/jobs/(\d+)", path)
        if m:
            job_num = m.group(1)
            req_id = req_id or job_num
            posting_id = posting_id or f"{tenant}:{job_num}"
            norm_url = f"https://{host}/jobs/{job_num}/job"
            ats_url = ats_url or norm_url
            return platform, norm_url, posting_id, req_id, ats_url

    # 11. Jobright (jobright.ai/jobs/info/<id>). The employer is not the site name.
    elif "jobright.ai" in host_clean:
        platform = platform or "jobright"
        m = re.search(r"/jobs/info/([A-Za-z0-9]{16,})", path)
        if m:
            posting_id = m.group(1)
            norm_url = f"https://jobright.ai/jobs/info/{posting_id}"
            return platform, norm_url, posting_id, req_id, ats_url

    # 12. SmartRecruiters
    elif "smartrecruiters.com" in host_clean:
        platform = platform or "smartrecruiters"
        m = re.search(r"/([^/]+)/(\d{6,}-[^/?#]+)", path)
        if m:
            company_slug, slug_id = m.group(1).lower(), m.group(2)
            req_num = slug_id.split("-")[0]
            req_id = req_id or req_num
            posting_id = posting_id or f"{company_slug}:{req_num}"
            norm_url = f"https://jobs.smartrecruiters.com/{company_slug}/{slug_id}"
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
    joined = " ".join(tokens) if tokens else (raw_company or "").lower().strip()
    return COMPANY_ALIASES.get(joined, joined)


def company_similarity(comp_a: str | None, comp_b: str | None) -> float:
    """
    Computes similarity between two normalized company names (0.0 to 1.0).
    Handles exact matches, parent/subsidiary prefixes ('cognizant' vs 'cognizant softvision'),
    and spacing/spelling variations ('jpmorgan' vs 'jp morgan').
    """
    a = normalize_company_name(comp_a or "")
    b = normalize_company_name(comp_b or "")
    if not a or not b:
        return 0.0
    if is_generic_company_name(a) or is_generic_company_name(b):
        return 0.0
    if a == b:
        return 1.0
    a_nospace = a.replace(" ", "")
    b_nospace = b.replace(" ", "")
    if a_nospace == b_nospace:
        return 0.98

    tokens_a = a.split()
    tokens_b = b.split()
    # Prefix / first-word brand match (e.g., 'cognizant' vs 'cognizant softvision')
    if tokens_a and tokens_b and tokens_a[0] == tokens_b[0] and len(tokens_a[0]) >= 4:
        return 0.92

    set_a, set_b = set(tokens_a), set(tokens_b)
    if set_a and set_b and (set_a.issubset(set_b) or set_b.issubset(set_a)):
        shorter = min(len(a_nospace), len(b_nospace))
        if shorter >= 4:
            return 0.90

    seq_ratio = SequenceMatcher(None, a_nospace, b_nospace).ratio()
    return round(seq_ratio, 2) if seq_ratio >= 0.80 else 0.0


ROLE_CORE_WORDS = {
    "engineer",
    "engineering",
    "developer",
    "development",
    "scientist",
    "science",
    "manager",
    "management",
    "architect",
    "architecture",
    "analyst",
    "designer",
    "specialist",
    "consultant",
    "lead",
    "leader",
    "director",
    "intern",
    "internship",
    "researcher",
    "research",
    "backend",
    "frontend",
    "fullstack",
    "stack",
    "platform",
    "data",
    "security",
    "cloud",
    "devops",
    "mlops",
    "software",
    "systems",
    "system",
    "machine",
    "learning",
    "artificial",
    "intelligence",
    "gen",
    "generative",
    "senior",
    "junior",
    "principal",
    "staff",
    "associate",
    "founding",
    "application",
    "applications",
    "infrastructure",
    "network",
    "database",
    "qa",
    "test",
    "automation",
    "product",
    "program",
    "project",
    "technical",
    "solutions",
}


def _strip_title_location_and_mode_suffixes(raw_title: str) -> str:
    text = (raw_title or "").lower().strip()
    if not text:
        return ""
    # 1. Remove parenthetical/bracketed qualifiers like (Full Time Only), (Remote), [Hybrid]
    text = re.sub(
        r"[\(\[][^)\]]*(?:full[-\s]*time|part[-\s]*time|remote|hybrid|on[-\s]*site|contract|w2|c2c|only|relocation|urgent|hiring|req|jr)[^)\]]*[\)\]]",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    # 2. Strip trailing location / employment segments after ' - ', ' | ', ' – ', ' — '
    parts = re.split(r"\s+(?:[-–—|]|\bor\b)\s+", text)
    kept_parts: list[str] = []
    for idx, part in enumerate(parts):
        if idx == 0:
            kept_parts.append(part)
            continue
        part_tokens = re.findall(r"[a-z0-9]+", part.lower())
        has_state_or_noise = any(
            t in US_STATE_CODES or t in CORE_TITLE_NOISE_TOKENS for t in part_tokens
        )
        has_role_word = any(t in ROLE_CORE_WORDS for t in part_tokens)
        if has_state_or_noise and not has_role_word:
            continue
        kept_parts.append(part)
    return " ".join(kept_parts)


def normalize_job_title(raw_title: str) -> str:
    text = _strip_title_location_and_mode_suffixes(raw_title)
    # Normalize compound words like full-stack, front-end, back-end, gen-ai
    text = re.sub(r"\bfull[-\s]+stack\b", "fullstack", text)
    text = re.sub(r"\bfront[-\s]+end\b", "frontend", text)
    text = re.sub(r"\bback[-\s]+end\b", "backend", text)
    text = re.sub(r"\bgen[-\s]+ai\b", "genai", text)
    text = re.sub(r"\bgenerative\s+ai\b", "genai", text)
    text = re.sub(r"\bgenerative\s+artificial\s+intelligence\b", "genai", text)
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = []
    for tok in text.split():
        mapped = TITLE_SYNONYMS.get(tok, tok)
        tokens.extend(mapped.split())
    return " ".join(tokens)


def extract_core_job_title(raw_or_norm_title: str | None) -> str:
    """
    Strips location suffixes, parenthetical tags ('(Full Time Only)', '(Remote)'),
    city+state pairs, and employment-mode noise so reposts with location/contract
    suffixes in the title produce the exact same core role title.
    """
    if not raw_or_norm_title:
        return ""
    norm_full = normalize_job_title(raw_or_norm_title)
    raw_tokens = norm_full.split()
    core_tokens: list[str] = []
    for i, tok in enumerate(raw_tokens):
        if tok in CORE_TITLE_NOISE_TOKENS or tok in US_STATE_CODES:
            continue
        # If this token is immediately followed by a US state code (e.g. 'irving tx', 'tampa fl')
        # and is not a core role word, it's a city name in a normalized title.
        if (
            i + 1 < len(raw_tokens)
            and raw_tokens[i + 1] in US_STATE_CODES
            and tok not in ROLE_CORE_WORDS
            and not tok.isdigit()
        ):
            continue
        core_tokens.append(tok)
    return " ".join(core_tokens) if core_tokens else norm_full


def compute_title_similarity(title_a: str | None, title_b: str | None) -> float:
    """
    Computes similarity between two job titles (0.0 to 1.0) using both full normalized
    titles and stripped core role titles.
    """
    if not title_a or not title_b:
        return 0.0
    if is_placeholder_or_confirmation_title(title_a) or is_placeholder_or_confirmation_title(
        title_b
    ):
        return 0.0

    norm_a = normalize_job_title(title_a)
    norm_b = normalize_job_title(title_b)
    if norm_a == norm_b:
        return 1.0

    core_a = extract_core_job_title(title_a)
    core_b = extract_core_job_title(title_b)
    if core_a and core_b and core_a == core_b:
        return 0.96

    tokens_a = {t for t in (core_a or norm_a).split() if len(t) >= 2 or t.isdigit()}
    tokens_b = {t for t in (core_b or norm_b).split() if len(t) >= 2 or t.isdigit()}
    if not tokens_a or not tokens_b:
        return 0.0

    inter = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    jaccard = inter / union if union > 0 else 0.0
    dice = (2.0 * inter) / (len(tokens_a) + len(tokens_b))
    containment = inter / min(len(tokens_a), len(tokens_b))

    # If one core title is completely contained in the other (e.g. "Applied AI Engineer" vs "Applied AI Engineer LLMs RAG")
    if containment >= 0.85 and inter >= 2:
        return round(max(dice, 0.88), 2)

    return round(max(jaccard, dice * 0.92), 2)


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
    # Replace bullet characters and replacement chars with spaces so words don't glue together
    text = re.sub(r"[\u00b7\u2022\u2023\u25aa\u25cf\ufffd•·▪]+", " ", text)
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
