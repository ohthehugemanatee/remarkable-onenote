"""End-to-end integration tests: filesystem change → router → backend.push().

All tests are RED: Pipeline.__init__ raises NotImplementedError.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from rmsync.pipeline import Pipeline
from tests.conftest import CapturingBackend, CapturingBackendWithIdentity


def _touch(info: dict[str, Any]) -> None:
    """Rewrite the .metadata file in place to trigger a watcher event."""
    p = info["metadata_path"]
    p.write_bytes(p.read_bytes())


async def _wait_for_push(backend: CapturingBackend, count: int = 1, timeout: float = 8.0) -> bool:
    """Poll until backend has at least `count` push calls or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(backend.push_calls) >= count:
            return True
        await asyncio.sleep(0.05)
    return False


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------

class TestEndToEnd:

    @pytest.mark.timeout(15)
    async def test_file_appears_calls_backend_push(
        self,
        make_doc: Callable[..., dict],
        store_root: Path,
        write_rules: Callable[[dict], Path],
        capturing_backend: CapturingBackend,
    ) -> None:
        """Document created in matching folder → backend.push() called with correct doc."""
        rules_path = write_rules({
            "config_version": 1,
            "rule_overlap": "first_match",
            "on_rule_exit": "archive",
            "rules": [
                {
                    "id": "work",
                    "match": {"folder": "/Work"},
                    "backends": [{"name": "null", "direction": "push"}],
                }
            ],
        })
        backends = {"null": capturing_backend}

        async with Pipeline(store_root, rules_path, backends, settle_seconds=0.1) as p:
            run_task = asyncio.create_task(p.run_until_cancelled())
            try:
                info = make_doc(uid="user1", visible_name="Work Note", parent="/Work")
                _touch(info)
                pushed = await _wait_for_push(capturing_backend)
            finally:
                run_task.cancel()
                try:
                    await run_task
                except asyncio.CancelledError:
                    pass

        assert pushed, "backend.push() was not called within timeout"
        assert any(c.meta.doc_id == info["doc_id"] for c in capturing_backend.push_calls)

    @pytest.mark.timeout(15)
    async def test_no_match_does_not_call_push(
        self,
        make_doc: Callable[..., dict],
        store_root: Path,
        write_rules: Callable[[dict], Path],
        capturing_backend: CapturingBackend,
    ) -> None:
        """Document in /Personal with only /Work rule → push not called."""
        rules_path = write_rules({
            "config_version": 1,
            "rule_overlap": "first_match",
            "on_rule_exit": "archive",
            "rules": [
                {
                    "id": "work",
                    "match": {"folder": "/Work"},
                    "backends": [{"name": "null", "direction": "push"}],
                }
            ],
        })
        backends = {"null": capturing_backend}

        async with Pipeline(store_root, rules_path, backends, settle_seconds=0.1) as p:
            run_task = asyncio.create_task(p.run_until_cancelled())
            try:
                info = make_doc(uid="user1", visible_name="Personal Note", parent="/Personal")
                _touch(info)
                await asyncio.sleep(1.5)
            finally:
                run_task.cancel()
                try:
                    await run_task
                except asyncio.CancelledError:
                    pass

        assert len(capturing_backend.push_calls) == 0

    @pytest.mark.timeout(15)
    async def test_union_mode_calls_multiple_backends(
        self,
        make_doc: Callable[..., dict],
        store_root: Path,
        write_rules: Callable[[dict], Path],
        capturing_backend: CapturingBackend,
        stable_backend: CapturingBackendWithIdentity,
    ) -> None:
        """union: document matching two rules dispatches to both backends."""
        rules_path = write_rules({
            "config_version": 1,
            "rule_overlap": "union",
            "on_rule_exit": "archive",
            "rules": [
                {
                    "id": "work",
                    "match": {"folder": "/Work"},
                    "backends": [{"name": "null", "direction": "push"}],
                },
                {
                    "id": "tagged-personal",
                    "match": {"tag": "personal"},
                    "backends": [{"name": "stable-null", "direction": "push"}],
                },
            ],
        })
        backends = {"null": capturing_backend, "stable-null": stable_backend}

        async with Pipeline(store_root, rules_path, backends, settle_seconds=0.1) as p:
            run_task = asyncio.create_task(p.run_until_cancelled())
            try:
                info = make_doc(
                    uid="user1",
                    visible_name="Work + Personal",
                    parent="/Work",
                    tags=("personal",),
                )
                _touch(info)
                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline:
                    if capturing_backend.push_calls and stable_backend.push_calls:
                        break
                    await asyncio.sleep(0.05)
            finally:
                run_task.cancel()
                try:
                    await run_task
                except asyncio.CancelledError:
                    pass

        assert len(capturing_backend.push_calls) >= 1
        assert len(stable_backend.push_calls) >= 1

    @pytest.mark.timeout(15)
    async def test_push_payload_contains_rm_bytes(
        self,
        make_doc: Callable[..., dict],
        store_root: Path,
        write_rules: Callable[[dict], Path],
        capturing_backend: CapturingBackend,
    ) -> None:
        """DocumentBundle.pages contains non-empty rm bytes for each page."""
        rules_path = write_rules({
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
        backends = {"null": capturing_backend}

        async with Pipeline(store_root, rules_path, backends, settle_seconds=0.1) as p:
            run_task = asyncio.create_task(p.run_until_cancelled())
            try:
                info = make_doc(uid="user1", page_count=2, parent="")
                _touch(info)
                await _wait_for_push(capturing_backend)
            finally:
                run_task.cancel()
                try:
                    await run_task
                except asyncio.CancelledError:
                    pass

        assert len(capturing_backend.push_calls) >= 1
        bundle = capturing_backend.push_calls[0]
        assert len(bundle.pages) == 2
        for page_id, rm_bytes in bundle.pages:
            assert rm_bytes, f"Page {page_id} has empty rm_bytes"


# ---------------------------------------------------------------------------
# 2. Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:

    @pytest.mark.timeout(20)
    async def test_same_doc_written_twice_pushes_twice(
        self,
        make_doc: Callable[..., dict],
        store_root: Path,
        write_rules: Callable[[dict], Path],
        capturing_backend: CapturingBackend,
    ) -> None:
        """Two sequential edits to the same document produce two push calls."""
        rules_path = write_rules({
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
        backends = {"null": capturing_backend}
        info = make_doc(uid="user1", parent="")

        async with Pipeline(store_root, rules_path, backends, settle_seconds=0.1) as p:
            run_task = asyncio.create_task(p.run_until_cancelled())
            try:
                _touch(info)
                await _wait_for_push(capturing_backend, count=1)
                _touch(info)
                await _wait_for_push(capturing_backend, count=2)
            finally:
                run_task.cancel()
                try:
                    await run_task
                except asyncio.CancelledError:
                    pass

        assert len(capturing_backend.push_calls) >= 2


# ---------------------------------------------------------------------------
# 3. Cold-start integration
# ---------------------------------------------------------------------------

class TestColdStart:

    @pytest.mark.timeout(15)
    async def test_pipeline_pushes_pre_existing_docs_on_start(
        self,
        make_doc: Callable[..., dict],
        store_root: Path,
        write_rules: Callable[[dict], Path],
        capturing_backend: CapturingBackend,
    ) -> None:
        """Documents existing before Pipeline starts are discovered and pushed."""
        info = make_doc(uid="user1", visible_name="Old Doc", parent="/Work")

        rules_path = write_rules({
            "config_version": 1,
            "rule_overlap": "first_match",
            "on_rule_exit": "archive",
            "rules": [
                {
                    "id": "work",
                    "match": {"folder": "/Work"},
                    "backends": [{"name": "null", "direction": "push"}],
                }
            ],
        })
        backends = {"null": capturing_backend}

        async with Pipeline(store_root, rules_path, backends, settle_seconds=0.1) as p:
            run_task = asyncio.create_task(p.run_until_cancelled())
            try:
                await _wait_for_push(capturing_backend)
            finally:
                run_task.cancel()
                try:
                    await run_task
                except asyncio.CancelledError:
                    pass

        assert any(
            c.meta.doc_id == info["doc_id"]
            for c in capturing_backend.push_calls
        ), "Pre-existing document must be pushed via cold-start scan"
