"""Initial schema for JobRecall (jobs, postings, job_events, dismissed_duplicates, requirement_analyses, user_profile, resumes)

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-10-14 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "resumes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("target_role", sa.String(length=200), nullable=True),
        sa.Column("pdf_ref", sa.Text(), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("parsed_json", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_resumes_content_hash", "resumes", ["content_hash"], unique=False)

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_raw", sa.String(length=300), nullable=False),
        sa.Column("company_norm", sa.String(length=300), nullable=False),
        sa.Column("title_raw", sa.String(length=300), nullable=False),
        sa.Column("title_norm", sa.String(length=300), nullable=False),
        sa.Column("location_raw", sa.String(length=300), nullable=True),
        sa.Column("location_norm", sa.String(length=300), nullable=True),
        sa.Column("work_mode", sa.String(length=50), nullable=True),
        sa.Column("employer_req_id", sa.String(length=150), nullable=True),
        sa.Column("canonical_ats_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_via_posting_id", sa.String(length=36), nullable=True),
        sa.Column("resume_id", sa.String(length=36), nullable=True),
        sa.Column("confirmation_ref", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_company_norm", "jobs", ["company_norm"], unique=False)
    op.create_index("ix_jobs_title_norm", "jobs", ["title_norm"], unique=False)
    op.create_index("ix_jobs_employer_req_id", "jobs", ["employer_req_id"], unique=False)
    op.create_index("ix_jobs_canonical_ats_url", "jobs", ["canonical_ats_url"], unique=False)
    op.create_index("ix_jobs_status", "jobs", ["status"], unique=False)

    op.create_table(
        "postings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("platform", sa.String(length=100), nullable=False),
        sa.Column("source_url_raw", sa.Text(), nullable=False),
        sa.Column("source_url_norm", sa.Text(), nullable=False),
        sa.Column("source_posting_id", sa.String(length=200), nullable=True),
        sa.Column("requisition_id", sa.String(length=150), nullable=True),
        sa.Column("description_raw", sa.Text(), nullable=True),
        sa.Column("cleaned_text_hash", sa.String(length=64), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_postings_job_id", "postings", ["job_id"], unique=False)
    op.create_index("ix_postings_platform", "postings", ["platform"], unique=False)
    op.create_index("ix_postings_source_url_norm", "postings", ["source_url_norm"], unique=False)
    op.create_index("ix_postings_source_posting_id", "postings", ["source_posting_id"], unique=False)
    op.create_index("ix_postings_requisition_id", "postings", ["requisition_id"], unique=False)
    op.create_index("ix_postings_cleaned_text_hash", "postings", ["cleaned_text_hash"], unique=False)
    op.create_index(
        "ix_postings_platform_source_id", "postings", ["platform", "source_posting_id"], unique=False
    )

    op.create_table(
        "job_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("old_status", sa.String(length=50), nullable=True),
        sa.Column("new_status", sa.String(length=50), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_events_job_id", "job_events", ["job_id"], unique=False)
    op.create_index("ix_job_events_created_at", "job_events", ["created_at"], unique=False)

    op.create_table(
        "dismissed_duplicates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("dismissed_url_norm", sa.Text(), nullable=True),
        sa.Column("dismissed_platform", sa.String(length=100), nullable=True),
        sa.Column("dismissed_posting_id", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dismissed_duplicates_job_id", "dismissed_duplicates", ["job_id"], unique=False)
    op.create_index(
        "ix_dismissed_duplicates_dismissed_url_norm",
        "dismissed_duplicates",
        ["dismissed_url_norm"],
        unique=False,
    )

    op.create_table(
        "requirement_analyses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("cleaned_text_hash", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=30), nullable=False),
        sa.Column("model_provider", sa.String(length=60), nullable=False),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column("extracted_json", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_requirement_analyses_cleaned_text_hash",
        "requirement_analyses",
        ["cleaned_text_hash"],
        unique=False,
    )

    op.create_table(
        "user_profile",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("is_us_citizen", sa.Boolean(), nullable=True),
        sa.Column("is_permanent_resident", sa.Boolean(), nullable=True),
        sa.Column("authorized_to_work_us", sa.Boolean(), nullable=True),
        sa.Column("requires_visa_sponsorship", sa.Boolean(), nullable=True),
        sa.Column("clearance_level", sa.String(length=60), nullable=True),
        sa.Column("highest_degree", sa.String(length=60), nullable=True),
        sa.Column("graduation_date", sa.String(length=20), nullable=True),
        sa.Column("years_experience_total", sa.Float(), nullable=True),
        sa.Column("preferred_work_modes", sa.Text(), nullable=True),
        sa.Column("current_state_code", sa.String(length=20), nullable=True),
        sa.Column("willing_to_relocate", sa.Boolean(), nullable=True),
        sa.Column("skills_and_domains_json", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("user_profile")
    op.drop_table("requirement_analyses")
    op.drop_table("dismissed_duplicates")
    op.drop_table("job_events")
    op.drop_table("postings")
    op.drop_table("jobs")
    op.drop_table("resumes")
