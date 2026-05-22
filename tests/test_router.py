"""Tests for rmsync.router — rule loading, validation, matching, overlap modes.

All tests are RED: Router.__init__ and Router.validate raise NotImplementedError.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from rmsync.router import Router
from rmsync.types import ConfigError, Direction, DocumentMeta, DocumentType


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def make_meta(
    doc_id: str = "doc-1",
    uid: str = "user1",
    visible_name: str = "My Note",
    doc_type: DocumentType = DocumentType.NOTEBOOK,
    parent: str = "/Work",
    tags: tuple[str, ...] = (),
) -> DocumentMeta:
    return DocumentMeta(
        doc_id=doc_id,
        uid=uid,
        visible_name=visible_name,
        doc_type=doc_type,
        parent=parent,
        tags=tags,
    )


def _rules(rules_list: list[dict], **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "config_version": 1,
        "rule_overlap": "first_match",
        "on_rule_exit": "archive",
        "rules": rules_list,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. Rule loading
# ---------------------------------------------------------------------------

class TestRuleLoading:

    def test_loads_valid_config(
        self,
        write_rules: Callable[[dict], Path],
        backend_registry: dict,
    ) -> None:
        p = write_rules(_rules([
            {
                "id": "work",
                "match": {"folder": "/Work"},
                "backends": [{"name": "null", "direction": "push"}],
            }
        ]))
        router = Router(p, backend_registry)
        assert router is not None

    def test_empty_rules_list_is_valid(
        self,
        write_rules: Callable[[dict], Path],
        backend_registry: dict,
    ) -> None:
        p = write_rules(_rules([]))
        router = Router(p, backend_registry)
        assert router.route(make_meta()) == []

    def test_missing_id_auto_generated(
        self,
        write_rules: Callable[[dict], Path],
        backend_registry: dict,
    ) -> None:
        p = write_rules(_rules([
            {
                "match": {"type": "notebook"},
                "backends": [{"name": "null", "direction": "push"}],
            }
        ]))
        router = Router(p, backend_registry)
        decisions = router.route(make_meta())
        assert len(decisions) == 1
        assert decisions[0].rule_id  # auto-generated, non-empty

    def test_constructor_raises_config_error_on_invalid_config(
        self,
        write_rules: Callable[[dict], Path],
        backend_registry: dict,
    ) -> None:
        """Router raises ConfigError (not generic Exception) for invalid rules."""
        p = write_rules(_rules([
            {
                "id": "bad",
                "match": {},   # CE-RULES-005: empty match
                "backends": [{"name": "null", "direction": "push"}],
            }
        ]))
        with pytest.raises(ConfigError):
            Router(p, backend_registry)


# ---------------------------------------------------------------------------
# 2. Validation errors (CE-RULES-NNN format)
# ---------------------------------------------------------------------------

class TestValidationErrors:

    def test_ce_rules_001_sync_requires_identity_stable(
        self, backend_registry: dict
    ) -> None:
        """CE-RULES-001: sync rejected when backend.identity_stable is False."""
        raw = _rules([
            {
                "id": "bad-sync",
                "match": {"folder": "/Work"},
                "backends": [{"name": "null", "direction": "sync"}],
            }
        ])
        errors = Router.validate(raw, backend_registry)
        codes = [e.code for e in errors]
        assert "CE-RULES-001" in codes
        err = next(e for e in errors if e.code == "CE-RULES-001")
        assert " | hint: " in str(err)
        assert " | doc: " in str(err)

    def test_ce_rules_001_sync_ok_with_identity_stable(
        self, rich_backend_registry: dict
    ) -> None:
        """CE-RULES-001: sync accepted when backend.identity_stable is True."""
        raw = _rules([
            {
                "id": "ok-sync",
                "match": {"folder": "/Work"},
                "backends": [{"name": "stable-null", "direction": "sync"}],
            }
        ])
        errors = Router.validate(raw, rich_backend_registry)
        assert "CE-RULES-001" not in [e.code for e in errors]

    def test_ce_rules_002_union_sync_without_priority(
        self, rich_backend_registry: dict
    ) -> None:
        """CE-RULES-002: union with overlapping sync rules missing sync_priority."""
        raw = _rules(
            [
                {
                    "id": "sync-a",
                    "match": {"folder": "/Work"},
                    "backends": [{"name": "stable-null", "direction": "sync"}],
                },
                {
                    "id": "sync-b",
                    "match": {"folder": "/Work"},
                    "backends": [{"name": "stable-null", "direction": "sync"}],
                },
            ],
            rule_overlap="union",
        )
        errors = Router.validate(raw, rich_backend_registry)
        assert "CE-RULES-002" in [e.code for e in errors]

    def test_ce_rules_002_union_sync_with_priority_ok(
        self, rich_backend_registry: dict
    ) -> None:
        """CE-RULES-002 not raised when sync_priority is declared."""
        raw = _rules(
            [
                {
                    "id": "sync-a",
                    "match": {"folder": "/Work"},
                    "backends": [
                        {"name": "stable-null", "direction": "sync", "sync_priority": 10}
                    ],
                },
                {
                    "id": "sync-b",
                    "match": {"folder": "/Work"},
                    "backends": [
                        {"name": "stable-null", "direction": "sync", "sync_priority": 5}
                    ],
                },
            ],
            rule_overlap="union",
        )
        errors = Router.validate(raw, rich_backend_registry)
        assert "CE-RULES-002" not in [e.code for e in errors]

    def test_ce_rules_003_mirror_pull_target_clashes_with_push_folder(
        self, rich_backend_registry: dict
    ) -> None:
        """CE-RULES-003: mirror_pull.target_folder cannot also be a push match.folder."""
        raw = _rules([
            {
                "id": "mirror",
                "match": {"folder": "/Inbox"},
                "backends": [
                    {
                        "name": "stable-null",
                        "direction": "mirror_pull",
                        "target_folder": "/rM-Inbox",
                    }
                ],
            },
            {
                "id": "push-conflict",
                "match": {"folder": "/rM-Inbox"},  # clashes with target_folder
                "backends": [{"name": "null", "direction": "push"}],
            },
        ])
        errors = Router.validate(raw, rich_backend_registry)
        assert "CE-RULES-003" in [e.code for e in errors]

    def test_ce_rules_004_unknown_backend(self, backend_registry: dict) -> None:
        """CE-RULES-004: backend name not in registry."""
        raw = _rules([
            {
                "id": "ghost",
                "match": {"folder": "/Work"},
                "backends": [{"name": "nonexistent", "direction": "push"}],
            }
        ])
        errors = Router.validate(raw, backend_registry)
        assert "CE-RULES-004" in [e.code for e in errors]

    def test_ce_rules_005_empty_match(self, backend_registry: dict) -> None:
        """CE-RULES-005: match clause with no criteria rejected."""
        raw = _rules([
            {
                "id": "no-match",
                "match": {},
                "backends": [{"name": "null", "direction": "push"}],
            }
        ])
        errors = Router.validate(raw, backend_registry)
        assert "CE-RULES-005" in [e.code for e in errors]

    def test_multiple_errors_collected_in_one_pass(
        self, backend_registry: dict
    ) -> None:
        """All CE-RULES errors are collected, not raised on first."""
        raw = _rules([
            {
                "id": "bad",
                "match": {},                              # CE-RULES-005
                "backends": [{"name": "ghost", "direction": "push"}],  # CE-RULES-004
            }
        ])
        errors = Router.validate(raw, backend_registry)
        assert len(errors) >= 2

    def test_error_format_matches_spec(self, backend_registry: dict) -> None:
        """Every ConfigError str matches CE-<DOMAIN>-<NNN>: ... | hint: ... | doc: ..."""
        raw = _rules([
            {
                "id": "bad",
                "match": {},
                "backends": [{"name": "null", "direction": "push"}],
            }
        ])
        errors = Router.validate(raw, backend_registry)
        for err in errors:
            s = str(err)
            assert err.code.startswith("CE-"), f"code should start with CE-: {s}"
            assert " | hint: " in s, f"missing '| hint:' in: {s}"
            assert " | doc: " in s, f"missing '| doc:' in: {s}"


# ---------------------------------------------------------------------------
# 3. Match logic
# ---------------------------------------------------------------------------

class TestMatchLogic:

    def test_folder_exact_match(
        self,
        write_rules: Callable[[dict], Path],
        backend_registry: dict,
    ) -> None:
        p = write_rules(_rules([
            {
                "id": "work",
                "match": {"folder": "/Work"},
                "backends": [{"name": "null", "direction": "push"}],
            }
        ]))
        router = Router(p, backend_registry)
        decisions = router.route(make_meta(parent="/Work"))
        assert len(decisions) == 1
        assert decisions[0].backend_name == "null"
        assert decisions[0].direction == Direction.PUSH

    def test_folder_glob_matches_subpath(
        self,
        write_rules: Callable[[dict], Path],
        backend_registry: dict,
    ) -> None:
        p = write_rules(_rules([
            {
                "id": "work-all",
                "match": {"folder": "/Work/*"},
                "backends": [{"name": "null", "direction": "push"}],
            }
        ]))
        router = Router(p, backend_registry)
        assert len(router.route(make_meta(parent="/Work/Projects"))) == 1

    def test_folder_no_match(
        self,
        write_rules: Callable[[dict], Path],
        backend_registry: dict,
    ) -> None:
        p = write_rules(_rules([
            {
                "id": "work",
                "match": {"folder": "/Work"},
                "backends": [{"name": "null", "direction": "push"}],
            }
        ]))
        router = Router(p, backend_registry)
        assert router.route(make_meta(parent="/Personal")) == []

    def test_tag_match(
        self,
        write_rules: Callable[[dict], Path],
        backend_registry: dict,
    ) -> None:
        p = write_rules(_rules([
            {
                "id": "personal",
                "match": {"tag": "personal"},
                "backends": [{"name": "null", "direction": "push"}],
            }
        ]))
        router = Router(p, backend_registry)
        doc = make_meta(parent="/Anywhere", tags=("personal", "diary"))
        decisions = router.route(doc)
        assert len(decisions) == 1
        assert decisions[0].rule_id == "personal"

    def test_tag_no_match(
        self,
        write_rules: Callable[[dict], Path],
        backend_registry: dict,
    ) -> None:
        p = write_rules(_rules([
            {
                "id": "personal",
                "match": {"tag": "personal"},
                "backends": [{"name": "null", "direction": "push"}],
            }
        ]))
        router = Router(p, backend_registry)
        assert router.route(make_meta(parent="/Work", tags=("work",))) == []

    def test_type_match(
        self,
        write_rules: Callable[[dict], Path],
        backend_registry: dict,
    ) -> None:
        p = write_rules(_rules([
            {
                "id": "pdfs",
                "match": {"type": "pdf"},
                "backends": [{"name": "null", "direction": "push"}],
            }
        ]))
        router = Router(p, backend_registry)
        assert len(router.route(make_meta(doc_type=DocumentType.PDF, parent=""))) == 1
        assert router.route(make_meta(doc_type=DocumentType.NOTEBOOK, parent="")) == []

    def test_combined_folder_and_type_requires_both(
        self,
        write_rules: Callable[[dict], Path],
        backend_registry: dict,
    ) -> None:
        p = write_rules(_rules([
            {
                "id": "work-notebooks",
                "match": {"folder": "/Work", "type": "notebook"},
                "backends": [{"name": "null", "direction": "push"}],
            }
        ]))
        router = Router(p, backend_registry)
        both = make_meta(parent="/Work", doc_type=DocumentType.NOTEBOOK)
        wrong_folder = make_meta(parent="/Personal", doc_type=DocumentType.NOTEBOOK)
        wrong_type = make_meta(parent="/Work", doc_type=DocumentType.PDF)
        assert len(router.route(both)) == 1
        assert router.route(wrong_folder) == []
        assert router.route(wrong_type) == []

    def test_title_regex_match(
        self,
        write_rules: Callable[[dict], Path],
        backend_registry: dict,
    ) -> None:
        p = write_rules(_rules([
            {
                "id": "meetings",
                "match": {"title_regex": "^Meeting.*"},
                "backends": [{"name": "null", "direction": "push"}],
            }
        ]))
        router = Router(p, backend_registry)
        assert len(router.route(make_meta(visible_name="Meeting 2026-05-22"))) == 1
        assert router.route(make_meta(visible_name="Personal Journal")) == []


# ---------------------------------------------------------------------------
# 4. Overlap modes
# ---------------------------------------------------------------------------

class TestOverlapModes:

    def test_first_match_uses_only_first_rule(
        self,
        write_rules: Callable[[dict], Path],
        backend_registry: dict,
    ) -> None:
        p = write_rules(_rules([
            {
                "id": "first",
                "match": {"folder": "/Work"},
                "backends": [{"name": "null", "direction": "push"}],
            },
            {
                "id": "second",
                "match": {"type": "notebook"},
                "backends": [{"name": "null", "direction": "archive"}],
            },
        ]))
        router = Router(p, backend_registry)
        decisions = router.route(make_meta(parent="/Work", doc_type=DocumentType.NOTEBOOK))
        assert len(decisions) == 1
        assert decisions[0].rule_id == "first"

    def test_union_collects_all_matching_rules(
        self,
        write_rules: Callable[[dict], Path],
        rich_backend_registry: dict,
    ) -> None:
        p = write_rules(_rules(
            [
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
            rule_overlap="union",
        ))
        router = Router(p, rich_backend_registry)
        doc = make_meta(parent="/Work", tags=("personal",))
        decisions = router.route(doc)
        backends = {d.backend_name for d in decisions}
        assert "null" in backends
        assert "stable-null" in backends

    def test_union_sync_priority_higher_wins(
        self,
        write_rules: Callable[[dict], Path],
        rich_backend_registry: dict,
    ) -> None:
        """When two sync rules target same backend, highest sync_priority wins."""
        p = write_rules(_rules(
            [
                {
                    "id": "low-prio",
                    "match": {"folder": "/Work"},
                    "backends": [
                        {"name": "stable-null", "direction": "sync", "sync_priority": 5}
                    ],
                },
                {
                    "id": "high-prio",
                    "match": {"type": "notebook"},
                    "backends": [
                        {"name": "stable-null", "direction": "sync", "sync_priority": 10}
                    ],
                },
            ],
            rule_overlap="union",
        ))
        router = Router(p, rich_backend_registry)
        decisions = router.route(make_meta(parent="/Work", doc_type=DocumentType.NOTEBOOK))
        stable = [d for d in decisions if d.backend_name == "stable-null"]
        assert len(stable) == 1
        assert stable[0].sync_priority == 10
        assert stable[0].rule_id == "high-prio"

    def test_error_mode_raises_on_multi_match(
        self,
        write_rules: Callable[[dict], Path],
        backend_registry: dict,
    ) -> None:
        p = write_rules(_rules(
            [
                {
                    "id": "a",
                    "match": {"folder": "/Work"},
                    "backends": [{"name": "null", "direction": "push"}],
                },
                {
                    "id": "b",
                    "match": {"type": "notebook"},
                    "backends": [{"name": "null", "direction": "push"}],
                },
            ],
            rule_overlap="error",
        ))
        router = Router(p, backend_registry)
        with pytest.raises(ConfigError):
            router.route(make_meta(parent="/Work", doc_type=DocumentType.NOTEBOOK))

    def test_error_mode_ok_on_single_match(
        self,
        write_rules: Callable[[dict], Path],
        backend_registry: dict,
    ) -> None:
        p = write_rules(_rules(
            [
                {
                    "id": "a",
                    "match": {"folder": "/Work"},
                    "backends": [{"name": "null", "direction": "push"}],
                },
                {
                    "id": "b",
                    "match": {"folder": "/Personal"},
                    "backends": [{"name": "null", "direction": "push"}],
                },
            ],
            rule_overlap="error",
        ))
        router = Router(p, backend_registry)
        decisions = router.route(make_meta(parent="/Work"))
        assert len(decisions) == 1


# ---------------------------------------------------------------------------
# 5. Hot reload
# ---------------------------------------------------------------------------

class TestHotReload:

    def test_reload_picks_up_new_rules(
        self,
        write_rules: Callable[[dict], Path],
        backend_registry: dict,
    ) -> None:
        p = write_rules(_rules([
            {
                "id": "work",
                "match": {"folder": "/Work"},
                "backends": [{"name": "null", "direction": "push"}],
            }
        ]))
        router = Router(p, backend_registry)
        assert router.route(make_meta(parent="/Personal")) == []

        p.write_text(yaml.dump(_rules([
            {
                "id": "work",
                "match": {"folder": "/Work"},
                "backends": [{"name": "null", "direction": "push"}],
            },
            {
                "id": "personal",
                "match": {"folder": "/Personal"},
                "backends": [{"name": "null", "direction": "push"}],
            },
        ])))
        router.reload()

        decisions = router.route(make_meta(parent="/Personal"))
        assert len(decisions) == 1
        assert decisions[0].rule_id == "personal"

    def test_reload_with_invalid_config_keeps_old(
        self,
        write_rules: Callable[[dict], Path],
        backend_registry: dict,
    ) -> None:
        p = write_rules(_rules([
            {
                "id": "work",
                "match": {"folder": "/Work"},
                "backends": [{"name": "null", "direction": "push"}],
            }
        ]))
        router = Router(p, backend_registry)
        assert len(router.route(make_meta(parent="/Work"))) == 1

        p.write_text("not: valid: yaml: [unclosed")
        with pytest.raises(Exception):
            router.reload()

        # Old config still active after failed reload
        assert len(router.route(make_meta(parent="/Work"))) == 1
