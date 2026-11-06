from fastapi.testclient import TestClient

from app.services.analyzer import verify_quote_in_text


def test_health_and_token_authentication(client: TestClient) -> None:
    # Health works and reports auth_valid=True with token
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["auth_valid"] is True

    # Calling protected endpoint with wrong token returns 401 AUTH_MISMATCH
    bad_resp = client.get("/api/v1/jobs", headers={"X-JobRecall-Token": "wrong-token"})
    assert bad_resp.status_code == 401
    assert bad_resp.json()["detail"]["code"] == "AUTH_MISMATCH"


def test_full_job_lifecycle_and_reversible_linking(
    client: TestClient, sample_postings: dict[str, dict]
) -> None:
    # 1. Create a reusable Resume (text + PDF reference)
    res_resp = client.post(
        "/api/v1/resumes",
        json={
            "label": "Backend Python v3",
            "target_role": "Backend Engineer",
            "pdf_ref": "C:\\Users\\wissa\\Resumes\\Backend_Python_v3.pdf",
            "content_text": "Wissa — Senior Backend Engineer. B.S. in Computer Science, Graduated May 2024. 4 years of experience in Python, FastAPI, PostgreSQL, Docker, Kubernetes, AWS.",
            "is_default": True,
            "auto_sync_profile": True,
        },
    )
    assert res_resp.status_code == 201
    resume_data = res_resp.json()
    resume_id = resume_data["id"]
    assert "Python" in resume_data["parsed"]["skills"]
    assert resume_data["parsed"]["highest_degree_detected"] == "bachelor"

    # Check that auto_sync_profile populated UserProfile degree & experience
    prof_resp = client.get("/api/v1/profile")
    assert prof_resp.status_code == 200
    assert prof_resp.json()["highest_degree"] == "bachelor"
    assert prof_resp.json()["years_experience_total"] == 4.0

    # 2. Save a job initially as 'saved'
    gh_job = sample_postings["tech_no_sponsorship"]
    create_resp = client.post(
        "/api/v1/jobs",
        json={
            **gh_job,
            "status": "saved",
            "note": "Looks interesting, checking visa policy first.",
        },
    )
    assert create_resp.status_code == 201
    created_job = create_resp.json()
    job_id = created_job["id"]
    assert created_job["status"] == "saved"
    assert created_job["applied_at"] is None

    # 3. Confirm application submission with resume_id and confirmation_ref
    status_resp = client.patch(
        f"/api/v1/jobs/{job_id}/status",
        json={
            "status": "applied",
            "resume_id": resume_id,
            "confirmation_ref": "CONF-99201",
            "note": "Submitted via Greenhouse portal.",
        },
    )
    assert status_resp.status_code == 200
    updated_job = status_resp.json()
    assert updated_job["status"] == "applied"
    assert updated_job["applied_at"] is not None
    assert updated_job["resume_id"] == resume_id
    assert updated_job["resume_label"] == "Backend Python v3"
    assert updated_job["resume_pdf_ref"] == "C:\\Users\\wissa\\Resumes\\Backend_Python_v3.pdf"
    assert updated_job["application_count"] == 1
    assert len(updated_job["applications"]) == 1
    first_application_id = updated_job["applications"][0]["id"]
    first_applied_at = updated_job["applications"][0]["applied_at"]

    # 4. Link a duplicate LinkedIn posting to the same Job, then test reversible unlink
    li_dup = sample_postings["cloudscale_linkedin_duplicate"]
    link_resp = client.post(f"/api/v1/jobs/{job_id}/link-posting", json=li_dup)
    assert link_resp.status_code == 200
    linked_job = link_resp.json()
    assert len(linked_job["postings"]) == 2

    second_posting_id = linked_job["postings"][1]["id"]
    unlink_resp = client.post(f"/api/v1/jobs/{job_id}/unlink-posting/{second_posting_id}")
    assert unlink_resp.status_code == 200
    assert len(unlink_resp.json()["postings"]) == 1

    # 5. Applying again keeps the first date and adds today's date plus the resume used
    again_resp = client.patch(
        f"/api/v1/jobs/{job_id}/status",
        json={
            "status": "applied",
            "resume_id": resume_id,
            "note": "Applied again after the posting was refreshed.",
        },
    )
    assert again_resp.status_code == 200
    again = again_resp.json()
    assert again["application_count"] == 2
    assert len(again["applications"]) == 2
    assert again["applications"][0]["note"] == "Applied again after the posting was refreshed."
    assert again["applications"][0]["resume_id"] == resume_id
    assert again["applied_at"] == again["applications"][0]["applied_at"]
    kept = next(item for item in again["applications"] if item["id"] == first_application_id)
    assert kept["applied_at"] == first_applied_at

    later_status = client.patch(
        f"/api/v1/jobs/{job_id}/status",
        json={"status": "interviewing", "note": "Recruiter screen scheduled."},
    )
    assert later_status.status_code == 200
    assert later_status.json()["status"] == "interviewing"
    assert later_status.json()["application_count"] == 2


def test_ai_analysis_quote_verification_cache_and_profile_comparison(
    client: TestClient, sample_postings: dict[str, dict]
) -> None:
    # Configure user profile: non-US citizen who requires sponsorship and has no clearance
    client.put(
        "/api/v1/profile",
        json={
            "is_us_citizen": False,
            "authorized_to_work_us": True,
            "requires_visa_sponsorship": True,
            "clearance_level": "none",
            "highest_degree": "bachelor",
            "years_experience_total": 4.0,
            "preferred_work_modes": ["remote", "hybrid"],
        },
    )

    # Add two resumes (one Backend Python, one Frontend React) to test Best Resume Recommendation
    client.post(
        "/api/v1/resumes",
        json={
            "label": "Frontend React v1",
            "pdf_ref": "C:\\Resumes\\Frontend.pdf",
            "content_text": "Frontend Engineer skilled in React, TypeScript, CSS, HTML.",
            "is_default": False,
            "auto_sync_profile": False,
        },
    )
    be_res = client.post(
        "/api/v1/resumes",
        json={
            "label": "Backend Python & K8s v2",
            "pdf_ref": "C:\\Resumes\\Backend.pdf",
            "content_text": "Backend Engineer skilled in Python, FastAPI, PostgreSQL, Docker, Kubernetes.",
            "is_default": True,
            "auto_sync_profile": False,
        },
    ).json()

    # Analyze Defense Clearance Posting
    defense = sample_postings["defense_clearance"]
    an_resp = client.post(
        "/api/v1/jobs/analyze",
        json={"description_text": defense["description_text"]},
    )
    assert an_resp.status_code == 200
    an_data = an_resp.json()
    assert an_data["cached"] is False
    ext = an_data["extraction"]
    assert ext["work_authorization"]["us_citizenship_required"] is True
    assert ext["work_authorization"]["quote_verified"] is True
    assert ext["sponsorship"]["sponsorship_offered"] == "no"
    assert ext["security_clearance"]["level"] == "ts_sci"

    comp = an_data["comparison"]
    assert comp["overall_fit"] == "possible_mismatch"
    assert comp["evaluations"]["work_authorization"]["fit_label"] == "possible_mismatch"
    assert comp["evaluations"]["sponsorship"]["fit_label"] == "possible_mismatch"
    assert comp["evaluations"]["security_clearance"]["fit_label"] == "possible_mismatch"
    assert comp["resume_recommendation"]["recommended_resume_id"] == be_res["id"]

    # Second call with same description hits content-hash cache (cached=True)
    an_cached = client.post(
        "/api/v1/jobs/analyze",
        json={"description_text": defense["description_text"]},
    )
    assert an_cached.status_code == 200
    assert an_cached.json()["cached"] is True

    # Resolving a job with that description automatically attaches cached_analysis!
    resolve_resp = client.post("/api/v1/jobs/resolve", json=defense)
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["cached_analysis"] is not None
    assert resolve_resp.json()["cached_analysis"]["cached"] is True


def test_uploaded_resume_pdf_is_stored_in_the_service(client: TestClient) -> None:
    pdf_bytes = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    resp = client.post(
        "/api/v1/resumes/extract-pdf",
        data={
            "label": "Stored Backend Resume",
            "target_role": "Backend Engineer",
            "content_text": (
                "Wissa Backend Engineer. Bachelor of Science in Computer Science. "
                "4 years of Python, FastAPI, and PostgreSQL experience."
            ),
            "is_default": "true",
            "auto_sync_profile": "false",
        },
        files={"file": ("Backend_v3.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["has_stored_pdf"] is True
    assert body["pdf_ref"] == "Backend_v3.pdf"
    assert body["pdf_size_bytes"] == len(pdf_bytes)

    download = client.get(f"/api/v1/resumes/{body['id']}/pdf")
    assert download.status_code == 200
    assert download.content == pdf_bytes
    assert "application/pdf" in download.headers["content-type"]

    deleted = client.delete(f"/api/v1/resumes/{body['id']}")
    assert deleted.status_code == 204
    missing = client.get(f"/api/v1/resumes/{body['id']}/pdf")
    assert missing.status_code == 404


def test_deterministic_quote_verifier_rejects_hallucinations() -> None:
    source = "Applicants must be authorized to work in the United States. Requires 3+ years of Python experience."
    assert verify_quote_in_text("Applicants must be authorized to work in the United States.", source) is True
    assert verify_quote_in_text("Must be a U.S. citizen with active Top Secret clearance.", source) is False


def test_ats_confirmation_page_recovery_on_save(client: TestClient) -> None:
    """When a user views a real job posting and then clicks 'Confirm Applied' on an ATS 'Thank You' page, the real job metadata is preserved."""
    real_job_payload = {
        "source_url": "https://cognizant.taleo.net/careersection/global/jobdetail.ftl?job=0005991122",
        "company": "Cognizant",
        "title": "Cloud & AI Software Engineer",
        "location": "Plano, TX (Hybrid)",
        "description_text": "Build cloud and AI solutions in Python and AWS. Hybrid 3-4 days a week in Plano, TX.",
    }
    # Step 1: Extension resolves the real job page when the user opens it
    res1 = client.post("/api/v1/jobs/resolve", json=real_job_payload)
    assert res1.status_code == 200
    assert res1.json()["match_state"] == "no_local_record"

    # Step 2: User finishes the application wizard on flow.jsf ("Thank You") and clicks Confirm Applied
    thank_you_save = client.post(
        "/api/v1/jobs",
        json={
            "source_url": "https://cognizant.taleo.net/careersection/careersection/candidateacquisition/flow.jsf",
            "company": "cognizant",
            "title": "Thank You",
            "status": "applied",
            "note": "Submitted via Taleo flow.",
        },
    )
    assert thank_you_save.status_code == 201
    saved_job = thank_you_save.json()
    assert saved_job["title_raw"] == "Cloud & AI Software Engineer"
    assert saved_job["status"] == "applied"
    assert saved_job["application_count"] == 1

    # Step 3: Revisiting the real job posting or a LinkedIn repost immediately recognizes it as Previously Applied
    revisit = client.post(
        "/api/v1/jobs/resolve",
        json={
            "source_url": "https://www.linkedin.com/jobs/view/8877665544/",
            "company": "Cognizant Technology Solutions",
            "title": "Cloud and AI Software Engineer - Plano, TX (Full Time)",
            "description_text": "Build cloud and AI solutions in Python and AWS. Hybrid 3-4 days a week in Plano, TX.",
        },
    )
    assert revisit.status_code == 200
    rev_data = revisit.json()
    assert rev_data["matched_job"] is not None
    assert rev_data["matched_job"]["id"] == saved_job["id"]
    assert rev_data["matched_job"]["status"] == "applied"

