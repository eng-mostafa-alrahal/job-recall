"""Add job_applications so each submission keeps its own date and resume.

Revision ID: 0002_job_applications
Revises: 0001_initial_schema
Create Date: 2026-10-15 00:00:00.000000

"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_job_applications"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "job_applications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resume_id", sa.String(length=36), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_applications_job_id", "job_applications", ["job_id"], unique=False)
    op.create_index(
        "ix_job_applications_applied_at", "job_applications", ["applied_at"], unique=False
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, applied_at, resume_id, confirmation_ref FROM jobs WHERE applied_at IS NOT NULL"
        )
    ).fetchall()
    for row in rows:
        conn.execute(
            sa.text(
                """
                INSERT INTO job_applications (id, job_id, applied_at, resume_id, note, created_at)
                VALUES (:id, :job_id, :applied_at, :resume_id, :note, :created_at)
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "job_id": row.id,
                "applied_at": row.applied_at,
                "resume_id": row.resume_id,
                "note": row.confirmation_ref,
                "created_at": row.applied_at,
            },
        )


def downgrade() -> None:
    op.drop_index("ix_job_applications_applied_at", table_name="job_applications")
    op.drop_index("ix_job_applications_job_id", table_name="job_applications")
    op.drop_table("job_applications")
