from sqlalchemy.orm import Session

from app.models.entities import DismissedDuplicate, Job, Posting, utcnow
from app.services.matcher import compute_description_similarity, resolve_posting_against_db
from app.services.normalizer import (
    clean_description_and_hash,
    company_similarity,
    compute_title_similarity,
    extract_core_job_title,
    is_generic_ats_flow_url,
    is_generic_company_name,
    is_placeholder_or_confirmation_title,
    normalize_company_name,
    normalize_job_title,
    normalize_posting_input,
    normalize_url_and_extract_ids,
)


def test_url_and_id_extraction_across_platforms() -> None:
    # 1. LinkedIn split-view query parameter
    platform, norm_url, pid, req, _ = normalize_url_and_extract_ids(
        "https://www.linkedin.com/jobs/search/?currentJobId=4100991122&keywords=backend"
    )
    assert platform == "linkedin"
    assert pid == "4100991122"
    assert norm_url == "https://www.linkedin.com/jobs/view/4100991122"

    # A stale id scraped from the page must not override currentJobId in the URL.
    _, _, pid, _, _ = normalize_url_and_extract_ids(
        "https://www.linkedin.com/jobs/search/?currentJobId=4100991122",
        explicit_posting_id="999",
    )
    assert pid == "4100991122"

    # 2. Indeed split-view vjk
    platform, norm_url, pid, _, _ = normalize_url_and_extract_ids(
        "https://www.indeed.com/jobs?q=python&vjk=ab12cd34ef56&from=searchOnHP"
    )
    assert platform == "indeed"
    assert pid == "ab12cd34ef56"
    assert norm_url == "https://www.indeed.com/viewjob?jk=ab12cd34ef56"

    # 3. Greenhouse board URL
    platform, norm_url, pid, req, ats = normalize_url_and_extract_ids(
        "https://boards.greenhouse.io/cloudscale/jobs/7721904?gh_src=linkedin"
    )
    assert platform == "greenhouse"
    assert pid == "cloudscale:7721904"
    assert req == "7721904"
    assert ats == "https://boards.greenhouse.io/cloudscale/jobs/7721904"

    # 4. Workday URL with _JR-1001 suffix
    platform, norm_url, pid, req, _ = normalize_url_and_extract_ids(
        "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Seattle-WA/Software-Engineer-II_JR-1001"
    )
    assert platform == "workday"
    assert req == "JR-1001"
    assert pid == "acme:JR-1001"

    # 5. Jobright detail URL. The id in the address bar wins over a stale scraped id.
    platform, norm_url, pid, req, _ = normalize_url_and_extract_ids(
        "https://www.jobright.ai/jobs/info/69900b1d6ea51a7610f2781f?utm_source=share",
        explicit_posting_id="stale-id",
    )
    assert platform == "jobright"
    assert pid == "69900b1d6ea51a7610f2781f"
    assert req is None
    assert norm_url == "https://jobright.ai/jobs/info/69900b1d6ea51a7610f2781f"
    assert is_generic_company_name(normalize_company_name("Jobright.ai")) is True
    assert is_generic_company_name(normalize_company_name("Jobright AI")) is True
    assert is_generic_company_name(normalize_company_name("jobright")) is True
    assert is_generic_company_name(normalize_company_name("Anthropic")) is False


def test_company_title_and_boilerplate_normalization() -> None:
    assert normalize_company_name("CloudScale Technologies, LLC.") == "cloudscale"
    assert normalize_company_name("Acme Corp, Inc.") == "acme"

    assert normalize_job_title("Sr. SWE II - Backend") == "senior software engineer 2 backend"
    assert normalize_job_title("Software Engineer 2 (Backend)") == "software engineer 2 backend"

    # Dynamic LinkedIn/Indeed boilerplate should not change the cleaned_text_hash
    text_day_2 = "Reposted 2 days ago. Over 100 applicants. Build Python APIs with FastAPI."
    text_day_5 = "Posted 5 days ago. Actively reviewing applicants. Build Python APIs with FastAPI."
    _, hash1 = clean_description_and_hash(text_day_2)
    _, hash2 = clean_description_and_hash(text_day_5)
    assert hash1 is not None
    assert hash1 == hash2


def test_tier1_exact_and_tier2_cross_platform_matching(
    db_session: Session, sample_postings: dict[str, dict]
) -> None:
    gh_post = sample_postings["tech_no_sponsorship"]
    li_dup = sample_postings["cloudscale_linkedin_duplicate"]

    norm_gh = normalize_posting_input(**gh_post)
    now = utcnow()
    job = Job(
        company_raw=gh_post["company"],
        company_norm=norm_gh.company_norm,
        title_raw=gh_post["title"],
        title_norm=norm_gh.title_norm,
        location_raw=gh_post["location"],
        location_norm=norm_gh.location_norm,
        work_mode=norm_gh.work_mode,
        employer_req_id=norm_gh.requisition_id,
        canonical_ats_url=norm_gh.canonical_ats_url,
        status="applied",
        applied_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(
        Posting(
            job_id=job.id,
            platform=norm_gh.platform,
            source_url_raw=gh_post["source_url"],
            source_url_norm=norm_gh.source_url_norm,
            source_posting_id=norm_gh.source_posting_id,
            requisition_id=norm_gh.requisition_id,
            description_raw=gh_post["description_text"],
            cleaned_text_hash=norm_gh.cleaned_text_hash,
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    db_session.commit()

    # Revisiting the same Greenhouse job with different tracking params -> EXACT_MATCH
    norm_revisit = normalize_posting_input(
        source_url="https://boards.greenhouse.io/cloudscale/jobs/7721904?utm_source=indeed",
        company="CloudScale",
        title="Software Engineer II - Backend",
    )
    res_exact = resolve_posting_against_db(db_session, norm_revisit)
    assert res_exact.match_tier == "EXACT_MATCH"
    assert res_exact.match_state == "previously_applied"
    assert res_exact.matched_job is not None
    assert res_exact.matched_job.id == job.id

    # Visiting the cross-posted LinkedIn duplicate -> STRONG_CANDIDATE (possible_duplicate)
    norm_li = normalize_posting_input(**li_dup)
    res_dup = resolve_posting_against_db(db_session, norm_li)
    assert res_dup.match_state == "possible_duplicate"
    assert res_dup.match_tier == "STRONG_CANDIDATE"
    assert len(res_dup.candidates) == 1
    assert res_dup.candidates[0].job.id == job.id
    assert res_dup.candidates[0].confidence >= 0.90

    # Dismissing the duplicate prevents future nagging
    db_session.add(
        DismissedDuplicate(
            job_id=job.id,
            dismissed_url_norm=norm_li.source_url_norm,
            dismissed_platform=norm_li.platform,
            dismissed_posting_id=norm_li.source_posting_id,
            created_at=utcnow(),
        )
    )
    db_session.commit()
    res_after_dismiss = resolve_posting_against_db(db_session, norm_li)
    assert res_after_dismiss.match_state == "no_local_record"


def test_revisit_same_linkedin_job_after_page_id_drift(db_session: Session) -> None:
    """Returning to the same title at the same company on LinkedIn recalls the application."""
    saved = normalize_posting_input(
        source_url="https://www.linkedin.com/jobs/search/?currentJobId=111",
        company="Northwind Labs",
        title="Backend Engineer",
        description_text="Build APIs with Python and FastAPI for the payments team.",
        platform="linkedin",
        source_posting_id="111",
    )
    now = utcnow()
    job = Job(
        company_raw="Northwind Labs",
        company_norm=saved.company_norm,
        title_raw="Backend Engineer",
        title_norm=saved.title_norm,
        status="applied",
        applied_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(
        Posting(
            job_id=job.id,
            platform="linkedin",
            source_url_raw="https://www.linkedin.com/jobs/search/?currentJobId=111",
            source_url_norm=saved.source_url_norm,
            source_posting_id="111",
            description_raw="Build APIs with Python and FastAPI for the payments team.",
            cleaned_text_hash=saved.cleaned_text_hash,
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    db_session.commit()

    revisit = normalize_posting_input(
        source_url="https://www.linkedin.com/jobs/search-results/?keywords=backend",
        company="Northwind Labs",
        title="Backend Engineer",
        description_text="A shorter capture of the same role.",
        platform="linkedin",
        source_posting_id="222",
    )
    outcome = resolve_posting_against_db(db_session, revisit)
    assert outcome.match_tier == "EXACT_MATCH"
    assert outcome.match_state == "previously_applied"
    assert outcome.matched_job is not None
    assert outcome.matched_job.id == job.id


def test_conflicting_requisition_id_guardrail(
    db_session: Session, sample_postings: dict[str, dict]
) -> None:
    """Two postings with identical company, title, and description but JR-1001 vs JR-1002 must NOT merge."""
    req1 = sample_postings["same_title_req_1001"]
    req2 = sample_postings["same_title_req_1002"]

    norm1 = normalize_posting_input(**req1)
    now = utcnow()
    job1 = Job(
        company_raw=req1["company"],
        company_norm=norm1.company_norm,
        title_raw=req1["title"],
        title_norm=norm1.title_norm,
        location_raw=req1["location"],
        location_norm=norm1.location_norm,
        employer_req_id=norm1.requisition_id,
        status="applied",
        applied_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(job1)
    db_session.flush()
    db_session.add(
        Posting(
            job_id=job1.id,
            platform=norm1.platform,
            source_url_raw=req1["source_url"],
            source_url_norm=norm1.source_url_norm,
            source_posting_id=norm1.source_posting_id,
            requisition_id=norm1.requisition_id,
            description_raw=req1["description_text"],
            cleaned_text_hash=norm1.cleaned_text_hash,
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    db_session.commit()

    norm2 = normalize_posting_input(**req2)
    outcome = resolve_posting_against_db(db_session, norm2)
    assert outcome.match_state == "no_local_record"
    assert outcome.match_tier == "NO_MATCH"
    assert outcome.candidates == []


def test_core_title_extraction_and_noisy_repost_matching(db_session: Session) -> None:
    """Titles with trailing locations/employment tags match clean reposted titles and self-heal generic company names."""
    noisy_title = "Gen AI Engineer - Irving, TX OR Tampa, FL (Full Time Only)"
    clean_title = "Generative AI Engineer"
    assert extract_core_job_title(noisy_title) == extract_core_job_title(clean_title)
    assert compute_title_similarity(noisy_title, clean_title) >= 0.95

    # Unicode bullets should be replaced with spaces rather than gluing words together
    desc_with_bullets = (
        "Job Description: GenAI Engineer · Design and build production-grade GenAI applications "
        "using Python, LangChain, LangGraph, and RAG architectures · Deploy scalable services."
    )
    desc_repost_with_boilerplate = (
        "About Virtusa: We are a global digital engineering company. "
        "GenAI Engineer - Design and build production-grade GenAI applications "
        "using Python, LangChain, LangGraph, and RAG architectures. Deploy scalable services. "
        "Equal Opportunity Employer: All qualified applicants will receive consideration."
    )
    assert compute_description_similarity(desc_with_bullets, desc_repost_with_boilerplate) >= 0.75

    # Simulate a job originally saved with generic board company 'linkedin' and noisy title
    saved_norm = normalize_posting_input(
        source_url="https://www.linkedin.com/jobs/search-results/?currentJobId=4478800260",
        company="linkedin",
        title=noisy_title,
        description_text=desc_with_bullets,
    )
    now = utcnow()
    job = Job(
        company_raw="linkedin",
        company_norm=saved_norm.company_norm,
        title_raw=noisy_title,
        title_norm=saved_norm.title_norm,
        status="applied",
        applied_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(
        Posting(
            job_id=job.id,
            platform=saved_norm.platform,
            source_url_raw="https://www.linkedin.com/jobs/search-results/?currentJobId=4478800260",
            source_url_norm=saved_norm.source_url_norm,
            source_posting_id=saved_norm.source_posting_id,
            description_raw=desc_with_bullets,
            cleaned_text_hash=saved_norm.cleaned_text_hash,
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    db_session.commit()

    # Later, the company reposts the job with a new LinkedIn ID (9991112223), real company name, and clean title
    repost_norm = normalize_posting_input(
        source_url="https://www.linkedin.com/jobs/view/9991112223/",
        company="Virtusa Corp",
        title=clean_title,
        description_text=desc_repost_with_boilerplate,
    )
    outcome = resolve_posting_against_db(
        db_session,
        repost_norm,
        raw_company="Virtusa Corp",
        raw_title=clean_title,
        raw_description=desc_repost_with_boilerplate,
    )
    assert outcome.match_state == "previously_applied"
    assert outcome.match_tier == "EXACT_MATCH"
    assert outcome.matched_job is not None
    assert outcome.matched_job.id == job.id
    # Verify that the generic 'linkedin' company was self-healed to 'Virtusa Corp'
    assert outcome.matched_job.company_norm == "virtusa"
    assert outcome.matched_job.company_raw == "Virtusa Corp"


def test_ats_thank_you_confirmation_page_matching_and_self_healing(db_session: Session) -> None:
    """A job saved from a Taleo/ATS 'Thank You' confirmation page matches the real Cognizant posting and heals its title."""
    assert is_placeholder_or_confirmation_title("Thank You") is True
    assert is_generic_ats_flow_url(
        "https://cognizant.taleo.net/careersection/careersection/candidateacquisition/flow.jsf"
    ) is True
    assert is_generic_company_name("linkedin") is True
    assert company_similarity("Cognizant", "Cognizant Technology Solutions") >= 0.85

    now = utcnow()
    job = Job(
        company_raw="cognizant",
        company_norm="cognizant",
        title_raw="Thank You",
        title_norm="thank you",
        status="applied",
        applied_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(
        Posting(
            job_id=job.id,
            platform="taleo",
            source_url_raw="https://cognizant.taleo.net/careersection/careersection/candidateacquisition/flow.jsf",
            source_url_norm="https://cognizant.taleo.net/careersection/careersection/candidateacquisition/flow.jsf",
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    db_session.commit()

    real_posting = normalize_posting_input(
        source_url="https://careers.cognizant.com/global-en/jobs/00061234561/software-engineer-early-career/",
        company="Cognizant Technology Solutions",
        title="Software Engineer - Early Career",
        description_text="Cognizant cannot provide sponsorship for U.S. work authorization. Bachelor's degree in Computer Science.",
    )
    outcome = resolve_posting_against_db(
        db_session,
        real_posting,
        raw_company="Cognizant Technology Solutions",
        raw_title="Software Engineer - Early Career",
        raw_description="Cognizant cannot provide sponsorship for U.S. work authorization. Bachelor's degree in Computer Science.",
    )
    assert outcome.match_state == "previously_applied"
    assert outcome.matched_job is not None
    assert outcome.matched_job.id == job.id
    assert outcome.matched_job.title_raw == "Software Engineer - Early Career"
    assert outcome.matched_job.postings[0].cleaned_text_hash is not None


def test_jobright_site_name_self_heals_to_employer(db_session: Session) -> None:
    """A Jobright capture saved as the board name is updated when the real employer is extracted."""
    url = "https://jobright.ai/jobs/info/69900b1d6ea51a7610f2781f"
    description = (
        "Anthropic is hiring a Design Engineer to build production web experiences. "
        "Required: Next.js, CSS, and a bachelor's degree or equivalent experience."
    )
    saved = normalize_posting_input(
        source_url=url,
        company="Jobright.ai",
        title="Design Engineer, Web",
        description_text=description,
    )
    assert saved.platform == "jobright"
    assert saved.company_norm == "jobright ai"
    assert is_generic_company_name(saved.company_norm) is True

    now = utcnow()
    job = Job(
        company_raw="Jobright.ai",
        company_norm=saved.company_norm,
        title_raw="Design Engineer, Web",
        title_norm=saved.title_norm,
        status="saved",
        created_at=now,
        updated_at=now,
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(
        Posting(
            job_id=job.id,
            platform=saved.platform,
            source_url_raw=url,
            source_url_norm=saved.source_url_norm,
            source_posting_id=saved.source_posting_id,
            description_raw=description,
            cleaned_text_hash=saved.cleaned_text_hash,
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    db_session.commit()

    reopened = normalize_posting_input(
        source_url=url,
        company="Anthropic",
        title="Design Engineer, Web",
        description_text=description,
        platform="jobright",
    )
    outcome = resolve_posting_against_db(
        db_session,
        reopened,
        raw_company="Anthropic",
        raw_title="Design Engineer, Web",
        raw_description=description,
    )
    assert outcome.match_state == "saved_in_progress"
    assert outcome.matched_job is not None
    assert outcome.matched_job.id == job.id
    assert outcome.matched_job.company_raw == "Anthropic"
    assert outcome.matched_job.company_norm == "anthropic"

