"""AstraIndexator code-only AccessZone producer identity.

Revision ID: 0006_access_zone_code_only
Revises: 0005_m8_delivery_batch_replay

AstraVector owns internal AccessZone UUID resolution. AstraIndexator keeps only
access_zone_code as producer/domain identity. delivery_checkpoint.resolved_access_zone_id
is deliberately retained as private downstream recovery evidence required by the
finalized AstraVector DocumentRef status wire.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_access_zone_code_only"
down_revision = "0005_m8_delivery_batch_replay"
branch_labels = None
depends_on = None

SCHEMA = "astra_indexator"


def upgrade() -> None:
    # Collapse the M8 dual-selector compatibility model into one producer-owned code.
    op.execute(
        f"""
        UPDATE {SCHEMA}.indexation_job
           SET access_zone_code = requested_access_zone_code
         WHERE access_zone_code IS NULL
           AND requested_access_zone_code IS NOT NULL
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM {SCHEMA}.indexation_job
                 WHERE access_zone_code IS NULL
            ) THEN
                RAISE EXCEPTION 'cannot migrate AstraIndexator to code-only AccessZone: rows without access_zone_code exist';
            END IF;
        END $$
        """
    )

    op.alter_column(
        "indexation_job",
        "access_zone_code",
        existing_type=sa.String(length=4),
        nullable=False,
        schema=SCHEMA,
    )
    op.drop_constraint(
        "requested_access_zone_selector_present",
        "indexation_job",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "requested_access_zone_code_format",
        "indexation_job",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_column("indexation_job", "requested_access_zone_id", schema=SCHEMA)
    op.drop_column("indexation_job", "requested_access_zone_code", schema=SCHEMA)
    op.drop_column("indexation_job", "access_zone_id", schema=SCHEMA)

    # M7 replay lineage follows the same single immutable selector.
    op.execute(
        f"""
        UPDATE {SCHEMA}.prepared_artifact_checkpoint
           SET requested_access_zone_code = j.access_zone_code
          FROM {SCHEMA}.indexation_job j
         WHERE {SCHEMA}.prepared_artifact_checkpoint.job_id = j.id
           AND {SCHEMA}.prepared_artifact_checkpoint.requested_access_zone_code IS NULL
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM {SCHEMA}.prepared_artifact_checkpoint
                 WHERE requested_access_zone_code IS NULL
            ) THEN
                RAISE EXCEPTION 'cannot migrate prepared artifacts to code-only AccessZone: rows without access_zone_code exist';
            END IF;
        END $$
        """
    )
    op.drop_constraint(
        "prepared_artifact_access_zone_selector_present",
        "prepared_artifact_checkpoint",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "prepared_artifact_access_zone_code_format",
        "prepared_artifact_checkpoint",
        schema=SCHEMA,
        type_="check",
    )
    op.alter_column(
        "prepared_artifact_checkpoint",
        "requested_access_zone_code",
        new_column_name="access_zone_code",
        existing_type=sa.String(length=4),
        nullable=False,
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "prepared_artifact_access_zone_code_format",
        "prepared_artifact_checkpoint",
        "access_zone_code ~ '^[0-9]{4}$'",
        schema=SCHEMA,
    )
    op.drop_column("prepared_artifact_checkpoint", "requested_access_zone_id", schema=SCHEMA)

    # Knowledge inventory is an AstraIndexator projection; it must expose only code.
    op.drop_column("knowledge_inventory", "access_zone_id", schema=SCHEMA)


def downgrade() -> None:
    op.add_column(
        "knowledge_inventory",
        sa.Column("access_zone_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )

    op.add_column(
        "prepared_artifact_checkpoint",
        sa.Column("requested_access_zone_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.drop_constraint(
        "prepared_artifact_access_zone_code_format",
        "prepared_artifact_checkpoint",
        schema=SCHEMA,
        type_="check",
    )
    op.alter_column(
        "prepared_artifact_checkpoint",
        "access_zone_code",
        new_column_name="requested_access_zone_code",
        existing_type=sa.String(length=4),
        nullable=True,
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "prepared_artifact_access_zone_code_format",
        "prepared_artifact_checkpoint",
        "requested_access_zone_code IS NULL OR requested_access_zone_code ~ '^[0-9]{4}$'",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "prepared_artifact_access_zone_selector_present",
        "prepared_artifact_checkpoint",
        "requested_access_zone_id IS NOT NULL OR requested_access_zone_code IS NOT NULL",
        schema=SCHEMA,
    )

    op.add_column(
        "indexation_job",
        sa.Column("access_zone_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "indexation_job",
        sa.Column("requested_access_zone_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "indexation_job",
        sa.Column("requested_access_zone_code", sa.String(length=4), nullable=True),
        schema=SCHEMA,
    )
    op.execute(
        f"UPDATE {SCHEMA}.indexation_job SET requested_access_zone_code = access_zone_code"
    )
    op.create_check_constraint(
        "requested_access_zone_code_format",
        "indexation_job",
        "requested_access_zone_code IS NULL OR requested_access_zone_code ~ '^[0-9]{4}$'",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "requested_access_zone_selector_present",
        "indexation_job",
        "requested_access_zone_id IS NOT NULL OR requested_access_zone_code IS NOT NULL",
        schema=SCHEMA,
    )
    op.alter_column(
        "indexation_job",
        "access_zone_code",
        existing_type=sa.String(length=4),
        nullable=True,
        schema=SCHEMA,
    )
