"""M9 knowledge inventory projection and source provenance freeze.

Revision ID: 0007_m9_knowledge_inventory_projection
Revises: 0006_m9_lifecycle_foundation
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_m9_knowledge_inventory_projection"
down_revision = "0006_m9_lifecycle_foundation"
branch_labels = None
depends_on = None

SCHEMA = "astra_indexator"
TABLE = "knowledge_inventory"
JOB_TABLE = "indexation_job"


def upgrade() -> None:
    # Source provenance freeze. source_file_name is the public/original name;
    # storage_object_* is the internal UUID-based storage identity.
    op.add_column(
        JOB_TABLE,
        sa.Column("storage_object_id", postgresql.UUID(as_uuid=True)),
        schema=SCHEMA,
    )
    op.add_column(JOB_TABLE, sa.Column("storage_object_name", sa.Text()), schema=SCHEMA)
    op.create_check_constraint(
        "ck_indexation_job_storage_object_name_non_blank",
        JOB_TABLE,
        "storage_object_name IS NULL OR length(trim(storage_object_name)) > 0",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_indexation_job_storage_identity_pair",
        JOB_TABLE,
        "(storage_object_id IS NULL AND storage_object_name IS NULL) OR "
        "(storage_object_id IS NOT NULL AND storage_object_name IS NOT NULL)",
        schema=SCHEMA,
    )

    op.add_column(TABLE, sa.Column("lifecycle_state", sa.String(length=32), nullable=True), schema=SCHEMA)
    op.add_column(
        TABLE,
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema=SCHEMA,
    )
    op.add_column(TABLE, sa.Column("requested_access_zone_code", sa.String(length=4)), schema=SCHEMA)
    op.add_column(
        TABLE,
        sa.Column("requested_access_zone_id", postgresql.UUID(as_uuid=True)),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("resolved_access_zone_id", postgresql.UUID(as_uuid=True)),
        schema=SCHEMA,
    )
    op.add_column(TABLE, sa.Column("requested_ttl_days", sa.Integer()), schema=SCHEMA)
    op.add_column(
        TABLE,
        sa.Column("ingestion_session_id", postgresql.UUID(as_uuid=True)),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("ready_to_activate", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema=SCHEMA,
    )

    op.add_column(
        TABLE,
        sa.Column("storage_object_id", postgresql.UUID(as_uuid=True)),
        schema=SCHEMA,
    )
    op.add_column(TABLE, sa.Column("storage_object_name", sa.Text()), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("source_uri", sa.Text()), schema=SCHEMA)

    op.add_column(TABLE, sa.Column("activated_at", sa.DateTime(timezone=True)), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("superseded_at", sa.DateTime(timezone=True)), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("cancelled_at", sa.DateTime(timezone=True)), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("deleted_at", sa.DateTime(timezone=True)), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("failed_at", sa.DateTime(timezone=True)), schema=SCHEMA)

    op.execute(
        sa.text(
            f"UPDATE {SCHEMA}.{TABLE} "
            "SET requested_access_zone_code = access_zone_code, "
            "resolved_access_zone_id = access_zone_id, "
            "lifecycle_state = 'BUILDING' "
            "WHERE requested_access_zone_code IS NULL"
        )
    )

    op.alter_column(TABLE, "access_zone_code", schema=SCHEMA, existing_type=sa.String(4), nullable=True)
    op.alter_column(TABLE, "lifecycle_state", schema=SCHEMA, existing_type=sa.String(32), nullable=False)

    op.create_check_constraint(
        "ck_knowledge_inventory_lifecycle_state_allowed",
        TABLE,
        "lifecycle_state IN ('BUILDING','READY','ACTIVE','SUPERSEDED','CANCEL_PENDING','CANCELLED','DELETE_PENDING','DELETED','FAILED')",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_knowledge_inventory_current_matches_active",
        TABLE,
        "(lifecycle_state = 'ACTIVE' AND is_current = true) OR "
        "(lifecycle_state <> 'ACTIVE' AND is_current = false)",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_knowledge_inventory_requested_access_zone_code_format",
        TABLE,
        "requested_access_zone_code IS NULL OR requested_access_zone_code ~ '^[0-9]{4}$'",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_knowledge_inventory_requested_access_zone_selector_present",
        TABLE,
        "requested_access_zone_id IS NOT NULL OR requested_access_zone_code IS NOT NULL",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_knowledge_inventory_requested_ttl_days_non_negative",
        TABLE,
        "requested_ttl_days IS NULL OR requested_ttl_days >= 0",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_knowledge_inventory_storage_object_name_non_blank",
        TABLE,
        "storage_object_name IS NULL OR length(trim(storage_object_name)) > 0",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_knowledge_inventory_storage_identity_pair",
        TABLE,
        "(storage_object_id IS NULL AND storage_object_name IS NULL) OR "
        "(storage_object_id IS NOT NULL AND storage_object_name IS NOT NULL)",
        schema=SCHEMA,
    )
    op.create_index(
        "uq_knowledge_inventory_current_active",
        TABLE,
        ["document_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("lifecycle_state = 'ACTIVE' AND is_current = true"),
    )


def downgrade() -> None:
    op.drop_index("uq_knowledge_inventory_current_active", table_name=TABLE, schema=SCHEMA)
    op.drop_constraint(
        "ck_knowledge_inventory_storage_identity_pair", TABLE, schema=SCHEMA, type_="check"
    )
    op.drop_constraint(
        "ck_knowledge_inventory_storage_object_name_non_blank", TABLE, schema=SCHEMA, type_="check"
    )
    op.drop_constraint(
        "ck_knowledge_inventory_requested_ttl_days_non_negative",
        TABLE,
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "ck_knowledge_inventory_requested_access_zone_selector_present",
        TABLE,
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "ck_knowledge_inventory_requested_access_zone_code_format",
        TABLE,
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "ck_knowledge_inventory_current_matches_active",
        TABLE,
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "ck_knowledge_inventory_lifecycle_state_allowed",
        TABLE,
        schema=SCHEMA,
        type_="check",
    )

    op.execute(
        sa.text(
            f"UPDATE {SCHEMA}.{TABLE} "
            "SET access_zone_code = COALESCE(access_zone_code, requested_access_zone_code, '0000')"
        )
    )
    op.alter_column(TABLE, "access_zone_code", schema=SCHEMA, existing_type=sa.String(4), nullable=False)

    for column in [
        "failed_at",
        "deleted_at",
        "cancelled_at",
        "superseded_at",
        "activated_at",
        "source_uri",
        "storage_object_name",
        "storage_object_id",
        "ready_to_activate",
        "ingestion_session_id",
        "requested_ttl_days",
        "resolved_access_zone_id",
        "requested_access_zone_id",
        "requested_access_zone_code",
        "is_current",
        "lifecycle_state",
    ]:
        op.drop_column(TABLE, column, schema=SCHEMA)

    op.drop_constraint(
        "ck_indexation_job_storage_identity_pair", JOB_TABLE, schema=SCHEMA, type_="check"
    )
    op.drop_constraint(
        "ck_indexation_job_storage_object_name_non_blank", JOB_TABLE, schema=SCHEMA, type_="check"
    )
    op.drop_column(JOB_TABLE, "storage_object_name", schema=SCHEMA)
    op.drop_column(JOB_TABLE, "storage_object_id", schema=SCHEMA)
