"""Combines Watcher + Router + backend registry into a runnable pipeline.

All public methods raise NotImplementedError — implementation is deferred.
"""
from __future__ import annotations

from pathlib import Path

from rmsync.plugins.base import BackendPlugin


class Pipeline:
    """Drives the watcher → router → backend dispatch loop.

    Usage::

        async with Pipeline(store_root, rules_path, backends) as p:
            await p.run_until_cancelled()
    """

    def __init__(
        self,
        store_root: Path,
        rules_path: Path,
        backends: dict[str, BackendPlugin],
        settle_seconds: float = 2.0,
    ) -> None:
        raise NotImplementedError

    async def __aenter__(self) -> "Pipeline":
        raise NotImplementedError

    async def __aexit__(self, *_: object) -> None:
        raise NotImplementedError

    async def run_until_cancelled(self) -> None:
        """Run the dispatch loop until asyncio.CancelledError."""
        raise NotImplementedError
