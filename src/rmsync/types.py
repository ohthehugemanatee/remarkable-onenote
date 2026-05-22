"""Core data types shared across rmsync modules."""
from __future__ import annotations

import enum
from pathlib import Path
from typing import Any

import attrs


class DocumentType(str, enum.Enum):
    NOTEBOOK = "notebook"
    PDF = "pdf"
    EPUB = "epub"
    FOLDER = "CollectionType"


class Direction(str, enum.Enum):
    PUSH = "push"
    MIRROR_PULL = "mirror_pull"
    SYNC = "sync"
    ARCHIVE = "archive"


class WatchEventKind(str, enum.Enum):
    MODIFIED = "modified"
    DELETED = "deleted"


@attrs.define(frozen=True)
class DocumentMeta:
    """Parsed document metadata from rmfakecloud's store."""
    doc_id: str
    uid: str
    visible_name: str
    doc_type: DocumentType
    parent: str          # resolved path string, e.g. "/Work" or "" for root
    tags: tuple[str, ...] = attrs.Factory(tuple)
    deleted: bool = False


@attrs.define(frozen=True)
class WatchEvent:
    """Emitted by Watcher after settle window and atomicity guards pass."""
    uid: str
    doc_id: str
    kind: WatchEventKind
    meta: DocumentMeta | None    # None on DELETED events
    store_root: Path


@attrs.define(frozen=True)
class RoutingDecision:
    """A single (backend, direction) routing outcome from the router."""
    rule_id: str
    backend_name: str
    direction: Direction
    sync_priority: int = 0


@attrs.define
class ConfigError(Exception):
    """Structured config-load error in CE-<DOMAIN>-<NNN> format."""
    code: str     # e.g. "CE-RULES-001"
    cause: str
    hint: str
    doc: str      # spec section anchor

    def __str__(self) -> str:
        return f"{self.code}: {self.cause} | hint: {self.hint} | doc: {self.doc}"


@attrs.define
class DocumentBundle:
    """Full document payload passed to backend.push()."""
    meta: DocumentMeta
    content: dict[str, Any]
    pages: list[tuple[str, bytes]]   # [(page_id, rm_bytes), ...]
