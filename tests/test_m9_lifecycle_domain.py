from __future__ import annotations

from uuid import UUID

import pytest

from astra_indexator.domain.lifecycle import (
    DocumentLifecycleState,
    DocumentVersionIdentity,
    InvalidLifecycleTransition,
    LifecycleTarget,
    require_lifecycle_transition,
)


def test_reindex_candidate_can_progress_building_ready_active() -> None:
    require_lifecycle_transition(DocumentLifecycleState.BUILDING, DocumentLifecycleState.READY)
    require_lifecycle_transition(DocumentLifecycleState.READY, DocumentLifecycleState.ACTIVE)


def test_active_version_can_only_be_superseded_or_deleted() -> None:
    require_lifecycle_transition(DocumentLifecycleState.ACTIVE, DocumentLifecycleState.SUPERSEDED)
    require_lifecycle_transition(DocumentLifecycleState.ACTIVE, DocumentLifecycleState.DELETE_PENDING)
    with pytest.raises(InvalidLifecycleTransition):
        require_lifecycle_transition(DocumentLifecycleState.ACTIVE, DocumentLifecycleState.FAILED)


def test_terminal_versions_cannot_reenter_building() -> None:
    for state in (
        DocumentLifecycleState.CANCELLED,
        DocumentLifecycleState.DELETED,
        DocumentLifecycleState.FAILED,
    ):
        with pytest.raises(InvalidLifecycleTransition):
            require_lifecycle_transition(state, DocumentLifecycleState.BUILDING)


def test_document_version_identity_requires_positive_numeric_version() -> None:
    with pytest.raises(ValueError, match="positive"):
        DocumentVersionIdentity(document_id=UUID(int=1), document_version=0)


def test_lifecycle_target_preserves_leading_zero_access_zone_code() -> None:
    target = LifecycleTarget(
        identity=DocumentVersionIdentity(document_id=UUID(int=1), document_version=7),
        requested_access_zone_code="0001",
        requested_access_zone_id=None,
    )
    assert target.requested_access_zone_code == "0001"


def test_lifecycle_target_rejects_noncanonical_access_zone_code() -> None:
    with pytest.raises(ValueError, match="\\^\\[0-9\\]\\{4\\}\\$"):
        LifecycleTarget(
            identity=DocumentVersionIdentity(document_id=UUID(int=1), document_version=1),
            requested_access_zone_code="1",
            requested_access_zone_id=None,
        )
