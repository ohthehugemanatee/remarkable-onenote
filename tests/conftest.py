"""Shared pytest fixtures for rmsync tests.

Three fixture families:
  - store_root / make_doc / make_rm_file: realistic rmfakecloud filesystem structures.
  - write_rules / minimal_rules: rules.yaml writers.
  - capturing_backend / stable_backend / backend_registry: working test-double backends.
"""
from __future__ import annotations

import json
import struct
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from rmsync.plugins.base import BackendPlugin, Capabilities, DeleteAck, MoveAck, PushAck
from rmsync.types import DocumentBundle, DocumentMeta, DocumentType


# ---------------------------------------------------------------------------
# Minimal valid .rm v6 binary (header + zero layers)
# ---------------------------------------------------------------------------

_RM_V6_MAGIC = b"reMarkable .lines file, version=6          \n"


def make_rm_bytes() -> bytes:
    """Return a minimal valid v6 .rm file (zero layers)."""
    return _RM_V6_MAGIC + struct.pack("<I", 0)


# ---------------------------------------------------------------------------
# Store builder fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store_root(tmp_path: Path) -> Path:
    """Empty rmfakecloud users directory."""
    root = tmp_path / "rmfakecloud" / "users"
    root.mkdir(parents=True)
    return root


@pytest.fixture
def make_doc(store_root: Path) -> Callable[..., dict[str, Any]]:
    """Factory: create a realistic document in the store.

    The `parent` argument accepts either:
    - "" (empty string) — root; stored as "" in .metadata
    - A UUID string — used directly as the parent doc_id in .metadata
    - A path string starting with "/" — auto-creates folder documents to build
      the hierarchy and stores the innermost folder's doc_id as parent.

    Returns a dict with keys: doc_id, uid, metadata_path, content_path,
    rm_paths, page_ids, parent_doc_id (the UUID stored in .metadata).
    """
    folder_cache: dict[tuple[str, str], str] = {}   # (uid, "/path") -> folder doc_id

    def _ensure_folder(uid: str, path: str) -> str:
        """Recursively create folder documents and return the leaf folder's doc_id."""
        if not path or path == "/":
            return ""
        key = (uid, path)
        if key in folder_cache:
            return folder_cache[key]

        parts = path.rstrip("/").split("/")
        # e.g. "/Work/Projects" → ["", "Work", "Projects"]
        parent_path = "/".join(parts[:-1]) or "/"
        parent_doc_id = _ensure_folder(uid, parent_path) if parent_path != "/" else ""

        folder_doc_id = str(uuid.uuid4())
        uid_dir = store_root / uid
        uid_dir.mkdir(exist_ok=True)

        folder_meta = {
            "visibleName": parts[-1],
            "type": "CollectionType",
            "parent": parent_doc_id,
            "lastModified": str(int(time.time() * 1000)),
            "deleted": False,
            "tags": [],
            "version": 1,
        }
        (uid_dir / f"{folder_doc_id}.metadata").write_text(json.dumps(folder_meta))
        (uid_dir / f"{folder_doc_id}.content").write_text(json.dumps({"pages": [], "pageCount": 0}))

        folder_cache[key] = folder_doc_id
        return folder_doc_id

    def _make(
        uid: str = "user1",
        visible_name: str = "My Note",
        doc_type: str = "DocumentType",
        parent: str = "",
        tags: tuple[str, ...] = (),
        page_count: int = 1,
        doc_id: str | None = None,
        deleted: bool = False,
    ) -> dict[str, Any]:
        if doc_id is None:
            doc_id = str(uuid.uuid4())

        uid_dir = store_root / uid
        uid_dir.mkdir(exist_ok=True)
        doc_dir = uid_dir / doc_id
        doc_dir.mkdir(exist_ok=True)

        # Resolve parent to a UUID
        if parent.startswith("/"):
            parent_doc_id = _ensure_folder(uid, parent)
        else:
            parent_doc_id = parent  # already a UUID or ""

        page_ids = [str(uuid.uuid4()) for _ in range(page_count)]

        rm_paths: list[Path] = []
        for page_id in page_ids:
            rm_path = doc_dir / f"{page_id}.rm"
            rm_path.write_bytes(make_rm_bytes())
            rm_paths.append(rm_path)

        content = {
            "schemaVersion": 2,
            "pages": page_ids,
            "pageCount": page_count,
        }
        content_path = uid_dir / f"{doc_id}.content"
        content_path.write_text(json.dumps(content))

        metadata = {
            "visibleName": visible_name,
            "type": doc_type,
            "parent": parent_doc_id,
            "lastModified": str(int(time.time() * 1000)),
            "deleted": deleted,
            "tags": list(tags),
            "version": 1,
        }
        metadata_path = uid_dir / f"{doc_id}.metadata"
        metadata_path.write_text(json.dumps(metadata))

        return {
            "doc_id": doc_id,
            "uid": uid,
            "metadata_path": metadata_path,
            "content_path": content_path,
            "rm_paths": rm_paths,
            "page_ids": page_ids,
            "parent_doc_id": parent_doc_id,
        }

    return _make


@pytest.fixture
def make_rm_file() -> Callable[[Path], Path]:
    """Factory: write a minimal v6 .rm file to a given path."""
    def _write(path: Path) -> Path:
        path.write_bytes(make_rm_bytes())
        return path
    return _write


# ---------------------------------------------------------------------------
# Rules fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def write_rules(tmp_path: Path) -> Callable[[dict[str, Any]], Path]:
    """Factory: write rules dict as YAML and return the Path."""
    rules_file = tmp_path / "rules.yaml"

    def _write(rules_dict: dict[str, Any]) -> Path:
        rules_file.write_text(yaml.dump(rules_dict))
        return rules_file

    return _write


@pytest.fixture
def minimal_rules(write_rules: Callable[[dict[str, Any]], Path]) -> Path:
    """A minimal valid rules.yaml: one catch-all notebook rule → null backend."""
    return write_rules({
        "config_version": 1,
        "rule_overlap": "first_match",
        "on_rule_exit": "archive",
        "rules": [
            {
                "id": "catch-all",
                "match": {"type": "notebook"},
                "backends": [{"name": "null", "direction": "push"}],
            }
        ],
    })


# ---------------------------------------------------------------------------
# Test-double backends (NOT the NullBackend stub from src/)
# ---------------------------------------------------------------------------

class CapturingBackend(BackendPlugin):
    """Working test double: records push calls, returns success."""
    name = "null"

    def __init__(self) -> None:
        self.push_calls: list[DocumentBundle] = []
        self._caps = Capabilities(
            preserves_strokes=True,
            supports_move=False,
            supports_pull=False,
            identity_stable=False,
            supports_archive_on_delete=False,
        )

    @property
    def capabilities(self) -> Capabilities:
        return self._caps

    def push(self, doc: DocumentBundle) -> PushAck:
        self.push_calls.append(doc)
        return PushAck(remote_id=doc.meta.doc_id, remote_hash="sha256:test")

    def pull_changes(self, cursor: str | None) -> tuple[list, str | None]:
        return [], None

    def move(self, doc_id: str, new_parent: str) -> MoveAck:
        return MoveAck(remote_id=doc_id, new_remote_parent=new_parent)

    def lookup(self, doc_id: str) -> str:
        return "unknown"


class CapturingBackendWithIdentity(CapturingBackend):
    """Variant with identity_stable=True and full capabilities."""
    name = "stable-null"

    def __init__(self) -> None:
        super().__init__()
        self._caps = Capabilities(
            preserves_strokes=True,
            supports_move=True,
            supports_pull=True,
            identity_stable=True,
            supports_archive_on_delete=True,
        )


@pytest.fixture
def capturing_backend() -> CapturingBackend:
    return CapturingBackend()


@pytest.fixture
def stable_backend() -> CapturingBackendWithIdentity:
    return CapturingBackendWithIdentity()


@pytest.fixture
def backend_registry(capturing_backend: CapturingBackend) -> dict[str, BackendPlugin]:
    return {"null": capturing_backend}


@pytest.fixture
def rich_backend_registry(
    capturing_backend: CapturingBackend,
    stable_backend: CapturingBackendWithIdentity,
) -> dict[str, BackendPlugin]:
    return {"null": capturing_backend, "stable-null": stable_backend}
