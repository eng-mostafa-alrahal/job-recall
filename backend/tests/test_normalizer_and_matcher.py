from sqlalchemy.orm import Session

from app.models.entities import DismissedDuplicate, Job, Posting, utcnow
from app.services.matcher import resolve_posting_against_db
from app.services.normalizer import (
    clean_description_and_hash,
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
