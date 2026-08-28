"""Persist immutable M8 delivery compatibility fingerprint.

Revision ID: 0007_m8_delivery_compatibility
Revises: 0006_access_zone_code_only
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_m8_delivery_compatibility"
down_revision = "0006_access_zone_code_only"
branch_labels = None
depends_on = None

SCHEMA = "astra_indexator"


def upgrade() -> None:
    op.add_column(
        "delivery_checkpoint",
        sa.Column("delivery_compatibility_sha256", sa.String(length=64), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "delivery_compatibility_sha256_format",
        "delivery_checkpoint",
        "delivery_compatibility_sha256 IS NULL OR "
        "delivery_compatibility_sha256 ~ '^[0-9a-f]{64}$'",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(
        "delivery_compatibility_sha256_format",
        "delivery_checkpoint",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_column("delivery_checkpoint", "delivery_compatibility_sha256", schema=SCHEMA)
