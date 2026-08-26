"""M9 source provenance identity.

Revision ID: 0008_m9_source_provenance_identity
Revises: 0007_m9_knowledge_inventory_projection
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_m9_source_provenance_identity"
down_revision = "0007_m9_knowledge_inventory_projection"
branch_labels = None
depends_on = None

SCHEMA = "astra_indexator"


def upgrade() -> None:
    for table in ("indexation_job", "knowledge_inventory"):
        op.add_column(
            table,
            sa.Column("storage_object_id", postgresql.UUID(as_uuid=True), nullable=True),
            schema=SCHEMA,
        )
        op.add_column(
            table,
            sa.Column("storage_object_name", sa.Text(), nullable=True),
            schema=SCHEMA,
        )
        op.create_check_constraint(
            f"ck_{table}_storage_object_name_non_blank",
            table,
            "storage_object_name IS NULL OR length(trim(storage_object_name)) > 0",
            schema=SCHEMA,
        )

    op.add_column(
        "knowledge_inventory",
        sa.Column("source_uri", sa.Text(), nullable=True),
        schema=SCHEMA,
    )

    op.execute(
        sa.text(
            f"UPDATE {SCHEMA}.knowledge_inventory ki "
            f"SET source_uri = job.source_uri "
            f"FROM {SCHEMA}.indexation_job job "
            "WHERE ki.job_id = job.id AND ki.source_uri IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_column("knowledge_inventory", "source_uri", schema=SCHEMA)

    for table in ("knowledge_inventory", "indexation_job"):
        op.drop_constraint(
            f"ck_{table}_storage_object_name_non_blank",
            table,
            schema=SCHEMA,
            type_="check",
        )
        op.drop_column(table, "storage_object_name", schema=SCHEMA)
        op.drop_column(table, "storage_object_id", schema=SCHEMA)
