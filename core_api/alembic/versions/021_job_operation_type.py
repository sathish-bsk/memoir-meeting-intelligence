"""Add operation_type column to transcription_jobs.

Revision ID: 021_job_operation_type
Revises: 020_crm_integration
Create Date: 2026-02-26 18:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "021_job_operation_type"
down_revision = "020_crm_integration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transcription_jobs",
        sa.Column(
            "operation_type",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("transcription_jobs", "operation_type")
