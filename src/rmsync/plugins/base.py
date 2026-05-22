"""BackendPlugin ABC and associated types."""
from __future__ import annotations

import abc
from typing import Any

import attrs

from rmsync.types import DocumentBundle, Direction


@attrs.define(frozen=True)
class Capabilities:
    preserves_strokes: bool
    supports_move: bool
    supports_pull: bool
    identity_stable: bool
    supports_archive_on_delete: bool


@attrs.define(frozen=True)
class PushAck:
    remote_id: str
    remote_hash: str


@attrs.define(frozen=True)
class MoveAck:
    remote_id: str
    new_remote_parent: str


@attrs.define(frozen=True)
class DeleteAck:
    remote_id: str
    archived: bool = False


class BackendPlugin(abc.ABC):
    name: str   # class-level; matches entry-point name and rules.yaml backend name

    @property
    @abc.abstractmethod
    def capabilities(self) -> Capabilities: ...

    @abc.abstractmethod
    def push(self, doc: DocumentBundle) -> PushAck: ...

    @abc.abstractmethod
    def pull_changes(self, cursor: str | None) -> tuple[list[Any], str | None]: ...

    @abc.abstractmethod
    def move(self, doc_id: str, new_parent: str) -> MoveAck: ...

    @abc.abstractmethod
    def lookup(self, doc_id: str) -> str: ...   # "gone" | "unknown" | "present:<hash>"

    def delete(self, doc_id: str) -> DeleteAck:
        """Archive into configured folder if capabilities allow, else hard delete."""
        raise NotImplementedError
