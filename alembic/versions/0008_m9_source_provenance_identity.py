"""M9 source provenance contract marker.

Revision ID: 0008_m9_source_provenance_identity
Revises: 0007_m9_knowledge_inventory_projection

The physical source-provenance columns are created by revision 0007. Revision 0008 is
kept as an explicit contract marker so already-published branch history has a stable
Alembic head without attempting to add the same columns twice.
"""

from __future__ import annotations

revision = "0008_m9_source_provenance_identity"
down_revision = "0007_m9_knowledge_inventory_projection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
