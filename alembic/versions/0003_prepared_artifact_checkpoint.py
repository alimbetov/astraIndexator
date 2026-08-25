"""M7 prepared artifact checkpoint.

Revision ID: 0003_prepared_checkpoint
Revises: 0002_acquisition_evidence
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_prepared_checkpoint"
down_revision = "0002_acquisition_evidence"
branch_labels = None
depends_on = None

SCHEMA = "astra_indexator"


def upgrade() -> None:
    op.create_table(
        "prepared_artifact_checkpoint",
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.indexation_job.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("artifact_id", sa.String(length=64), nullable=False),
        sa.Column("manifest_uri", sa.Text(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("compatibility_sha256", sa.String(length=64), nullable=False),
        sa.Column("element_count", sa.BigInteger(), nullable=False),
        sa.Column("fragment_count", sa.BigInteger(), nullable=False),
        sa.Column("lease_generation", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("lease_generation > 0", name="prepared_artifact_lease_generation_positive"),
        sa.CheckConstraint("element_count >= 0", name="prepared_artifact_element_count_non_negative"),
        sa.CheckConstraint("fragment_count >= 0", name="prepared_artifact_fragment_count_non_negative"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_prepared_artifact_checkpoint_artifact_id",
        "prepared_artifact_checkpoint",
        ["artifact_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_prepared_artifact_checkpoint_artifact_id",
        table_name="prepared_artifact_checkpoint",
        schema=SCHEMA,
    )
    op.drop_table("prepared_artifact_checkpoint", schema=SCHEMA)
