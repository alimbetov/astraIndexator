from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

_ACCESS_ZONE_RE = re.compile(r"^[0-9]{4}$")


class KnowledgeType(StrEnum):
    GENERAL = "GENERAL"
    CORPORATE = "CORPORATE"
    REGULATORY = "REGULATORY"
    LEGAL = "LEGAL"
    FINANCE = "FINANCE"
    HR = "HR"
    TECHNICAL = "TECHNICAL"
    OPERATIONS = "OPERATIONS"
    SECURITY = "SECURITY"
    ARCHIVE = "ARCHIVE"


CANONICAL_ACCESS_ZONES: dict[KnowledgeType, str] = {
    KnowledgeType.GENERAL: "0000",
    KnowledgeType.CORPORATE: "0100",
    KnowledgeType.REGULATORY: "0200",
    KnowledgeType.LEGAL: "0300",
    KnowledgeType.FINANCE: "0400",
    KnowledgeType.HR: "0500",
    KnowledgeType.TECHNICAL: "0600",
    KnowledgeType.OPERATIONS: "0700",
    KnowledgeType.SECURITY: "0800",
    KnowledgeType.ARCHIVE: "0900",
}


@dataclass(frozen=True, slots=True)
class AccessZoneCode:
    value: str

    def __post_init__(self) -> None:
        if not _ACCESS_ZONE_RE.fullmatch(self.value):
            raise ValueError("accessZoneCode must be exactly four ASCII digits")

    @property
    def is_canonical_root(self) -> bool:
        return self.value in CANONICAL_ACCESS_ZONES.values()

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class AccessZoneIntent:
    """Producer-owned AccessZone selector for AstraIndexator.

    AstraIndexator accepts and preserves only the public immutable AccessZoneCode.
    AstraVector owns any internal code-to-UUID resolution required by its storage,
    retrieval, GraphRAG, TTL, or lifecycle implementation.
    """

    access_zone_code: AccessZoneCode


@dataclass(frozen=True, slots=True)
class TtlIntent:
    """Producer TTL intent for the AstraVector session-ingestion path."""

    ttl_days: int = 0

    def __post_init__(self) -> None:
        if self.ttl_days < 0:
            raise ValueError("ttlDays must be non-negative")

    @property
    def inherits_zone_policy(self) -> bool:
        return self.ttl_days == 0


@dataclass(frozen=True, slots=True)
class DeliveryIntent:
    """Security/lifecycle context that must survive M1→M8 and M7 replay."""

    access_zone: AccessZoneIntent
    ttl: TtlIntent


@dataclass(frozen=True, slots=True)
class DocumentIdentity:
    document_id: UUID
    document_version: int

    def __post_init__(self) -> None:
        if self.document_version <= 0:
            raise ValueError("documentVersion must be a positive integer")
