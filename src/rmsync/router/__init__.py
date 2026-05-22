"""YAML-driven rule engine that maps document metadata to routing decisions.

All public methods raise NotImplementedError — implementation is deferred.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from rmsync.plugins.base import BackendPlugin
from rmsync.types import ConfigError, DocumentMeta, RoutingDecision


class Router:
    """Loads rules.yaml, validates it, and routes documents to backends.

    Usage::

        router = Router(rules_path, backend_registry)
        decisions = router.route(doc_meta)
        router.reload()   # hot-reload after file change
    """

    def __init__(
        self,
        rules_path: Path,
        backend_registry: dict[str, BackendPlugin],
    ) -> None:
        raise NotImplementedError

    def route(self, doc: DocumentMeta) -> list[RoutingDecision]:
        """Return routing decisions for doc under current rules.

        Returns [] if no rule matches.
        Raises ConfigError if overlap mode is 'error' and >1 rule matches.
        """
        raise NotImplementedError

    def reload(self) -> None:
        """Re-read rules_path and re-validate.

        On validation error: keep the previous good config and raise ConfigError.
        """
        raise NotImplementedError

    @staticmethod
    def validate(
        rules_raw: dict[str, Any],
        backend_registry: dict[str, BackendPlugin],
    ) -> list[ConfigError]:
        """Validate a parsed YAML dict against CE-RULES-001..005.

        Returns a list of ConfigErrors (empty list = valid). Does not mutate state.
        """
        raise NotImplementedError
