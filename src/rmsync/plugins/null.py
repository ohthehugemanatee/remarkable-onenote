"""NullBackend — registered entry point, raises NotImplementedError on all ops."""
from __future__ import annotations

from rmsync.plugins.base import BackendPlugin, Capabilities, DeleteAck, MoveAck, PushAck
from rmsync.types import DocumentBundle


class NullBackend(BackendPlugin):
    name = "null"

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            preserves_strokes=True,
            supports_move=False,
            supports_pull=False,
            identity_stable=False,
            supports_archive_on_delete=False,
        )

    def push(self, doc: DocumentBundle) -> PushAck:
        raise NotImplementedError

    def pull_changes(self, cursor: str | None) -> tuple[list, str | None]:
        raise NotImplementedError

    def move(self, doc_id: str, new_parent: str) -> MoveAck:
        raise NotImplementedError

    def lookup(self, doc_id: str) -> str:
        raise NotImplementedError
