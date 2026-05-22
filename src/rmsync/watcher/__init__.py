"""Inotify-based watcher over an rmfakecloud user store.

Emits WatchEvents after settle window + atomicity guards pass.
All public methods raise NotImplementedError — implementation is deferred.
"""
from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

from rmsync.types import WatchEvent


SETTLE_SECONDS: float = 2.0
MAX_ATOMICITY_RETRIES: int = 5


class Watcher:
    """Async context manager and async iterator of WatchEvents.

    Usage::

        async with Watcher(store_root, settle_seconds=2.0) as w:
            async for event in w:
                handle(event)
    """

    def __init__(
        self,
        store_root: Path,
        settle_seconds: float = SETTLE_SECONDS,
    ) -> None:
        raise NotImplementedError

    async def __aenter__(self) -> "Watcher":
        raise NotImplementedError

    async def __aexit__(self, *_: object) -> None:
        raise NotImplementedError

    def __aiter__(self) -> AsyncIterator[WatchEvent]:
        raise NotImplementedError

    async def __anext__(self) -> WatchEvent:
        raise NotImplementedError

    async def cold_scan(self) -> list[WatchEvent]:
        """Scan store_root and return synthetic MODIFIED events for every document found."""
        raise NotImplementedError
