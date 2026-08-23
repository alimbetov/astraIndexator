"""M3 acquisition evidence.

Revision ID: 0002_acquisition_evidence
Revises: 0001_initial_schema
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_acquisition_evidence"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None

SCHEMA = "astra_indexator"


def upgrade() -> None:
    op.add_column("indexation_job", sa.Column("source_etag", sa.String(255)), schema=SCHEMA)
    op.add_column("indexation_job", sa.Column("source_version_id", sa.String(255)), schema=SCHEMA)
    op.add_column("indexation_job", sa.Column("source_detected_format", sa.String(32)), schema=SCHEMA)
    op.add_column("indexation_job", sa.Column("source_detected_content_type", sa.String(255)), schema=SCHEMA)
    op.add_column("indexation_job", sa.Column("source_validation_profile", sa.String(64)), schema=SCHEMA)
    op.add_column("indexation_job", sa.Column("source_acquired_at", sa.DateTime(timezone=True)), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("indexation_job", "source_acquired_at", schema=SCHEMA)
    op.drop_column("indexation_job", "source_validation_profile", schema=SCHEMA)
    op.drop_column("indexation_job", "source_detected_content_type", schema=SCHEMA)
    op.drop_column("indexation_job", "source_detected_format", schema=SCHEMA)
    op.drop_column("indexation_job", "source_version_id", schema=SCHEMA)
    op.drop_column("indexation_job", "source_etag", schema=SCHEMA)
