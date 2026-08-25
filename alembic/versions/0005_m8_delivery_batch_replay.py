"""M8.2.5 durable DeliveryBatch replay constraints.

Revision ID: 0005_m8_delivery_batch_replay
Revises: 0004_m8_zone_ttl
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_m8_delivery_batch_replay"
down_revision = "0004_m8_zone_ttl"
branch_labels = None
depends_on = None

SCHEMA = "astra_indexator"


def upgrade() -> None:
    op.drop_constraint(
        "ck_delivery_batch_block_count_non_negative",
        "delivery_batch",
        schema=SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        "ck_delivery_batch_block_count_positive",
        "delivery_batch",
        "block_count > 0",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_delivery_batch_status_allowed",
        "delivery_batch",
        "status IN ('PREPARED','ACCEPTED')",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_delivery_batch_accepted_at_matches_status",
        "delivery_batch",
        "(status = 'PREPARED' AND accepted_at IS NULL) OR "
        "(status = 'ACCEPTED' AND accepted_at IS NOT NULL)",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_delivery_batch_accepted_at_matches_status",
        "delivery_batch",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "ck_delivery_batch_status_allowed",
        "delivery_batch",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "ck_delivery_batch_block_count_positive",
        "delivery_batch",
        schema=SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        "ck_delivery_batch_block_count_non_negative",
        "delivery_batch",
        "block_count >= 0",
        schema=SCHEMA,
    )
