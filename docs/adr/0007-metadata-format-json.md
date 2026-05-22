# ADR-0007: `.metadata` files are JSON, not YAML

Status: Accepted

## Context

Spec §2.2 described rmfakecloud's `<uuid>.metadata` files as YAML:

> `<uuid>.metadata` (YAML) — title, type, parent, last modified, deleted flag, tags

This was incorrect. rmfakecloud serialises all document metadata using Go's `encoding/json` package. The `.metadata` files on disk are JSON objects, for example:

```json
{
  "visibleName": "My Note",
  "type": "DocumentType",
  "parent": "a1b2c3d4-...",
  "lastModified": "1716390000000",
  "deleted": false,
  "tags": [],
  "version": 1
}
```

The mistake was caught during test authoring: the `conftest.py` fixture needed to write realistic `.metadata` files and the JSON format was verified against rmfakecloud's source (`pkg/storage/filestorage.go` uses `json.Marshal`).

## Decision

Accept rmfakecloud's actual format (JSON). Correct spec §2.2 accordingly. All rmsync code that reads `.metadata` files uses a JSON parser (`json.loads` in Python). No YAML parser is involved in reading store files.

## Consequences

- `rmsync.watcher` reads `.metadata` with `json.loads`, not a YAML parser.
- Test fixtures write `.metadata` with `json.dumps`.
- `pyyaml` remains a dependency for `rules.yaml` only — operator-facing config files are YAML; rmfakecloud store files are JSON.
- No architectural impact. The format choice is internal to rmfakecloud and rmsync adapts to it.
