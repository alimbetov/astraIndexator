from __future__ import annotations

from uuid import uuid4

import pytest

from astra_indexator.persistence.repository import NewIndexationJob


def _command(**overrides: object) -> NewIndexationJob:
    values: dict[str, object] = {
        "producer_request_id": uuid4(),
        "document_id": uuid4(),
        "document_version": 1,
        "source_uri": "seaweed://documents/internal.pdf",
        "access_zone_code": "0001",
        "source_file_name": "Публичное имя документа.pdf",
    }
    values.update(overrides)
    return NewIndexationJob(**values)  # type: ignore[arg-type]


def test_public_filename_and_internal_storage_name_remain_independent() -> None:
    storage_id = uuid4()
    command = _command(
        storage_object_id=storage_id,
        storage_object_name=f"{storage_id}.pdf",
    )

    assert command.source_file_name == "Публичное имя документа.pdf"
    assert command.storage_object_name == f"{storage_id}.pdf"
    assert command.source_file_name != command.storage_object_name


def test_partial_internal_storage_identity_is_rejected() -> None:
    with pytest.raises(ValueError, match="must either both be set or both be unset"):
        _command(storage_object_id=uuid4())

    with pytest.raises(ValueError, match="must either both be set or both be unset"):
        _command(storage_object_name=f"{uuid4()}.pdf")


def test_blank_public_or_storage_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="source_file_name must not be blank"):
        _command(source_file_name="   ")

    storage_id = uuid4()
    with pytest.raises(ValueError, match="storage_object_name must not be blank"):
        _command(storage_object_id=storage_id, storage_object_name="   ")
