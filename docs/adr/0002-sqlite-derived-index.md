# ADR-0002: SQLite state store as a derived index

Status: accepted (2026-05-21)

## Context

rmsync needs persistent state for document identity, move tracking, conflict bookkeeping, backend cursors, mirror-pull tombstones, and routing decisions. The original (pre-pivot) plan called for a two-phase WAL on SQLite with a `PREPARED → VALIDATED → COMMITTED → FAILED` transition state machine, because the state store was the authoritative record of in-flight protocol operations on the tablet side.

After ADR-0001, the cloud layer is rmfakecloud and the tablet's source of truth lives there. rmsync's state DB no longer participates in tablet-protocol durability — it indexes data that already exists durably elsewhere.

## Decision

The state store is a SQLite database in WAL mode, treated as a **derived index** over rmfakecloud's filesystem store and each backend's remote state. The DB MAY be deleted at any time and rebuilt by:

1. Cold-start scan of rmfakecloud's user store (re-derives `documents`, `representations`).
2. Replaying the current `rules.yaml` against each document (re-derives `routing_decisions`).
3. Running each backend's `pull_changes(cursor=None)` (re-derives `backend_state`, `backend_cursors`).

No two-phase WAL. No PREPARED reaper. Ordinary SQLite transactions with WAL-mode durability are sufficient.

Tombstones (`mirror_pull_tombstones`) are the one exception: they cannot be rebuilt from upstream sources because the "deleted on tablet" signal is ephemeral. Tombstones MUST be backed up or transferred when migrating deployments.

## Consequences

### Positive

- Drastically simpler state-store code. No state machine over WAL rows. No reaper.
- Operator recovery is trivial: `rm state.db && systemctl restart rmsync` rebuilds everything important.
- Backups need only protect the tombstone table; everything else is reconstructible.
- Tests can use ephemeral SQLite without contaminating the model.

### Negative

- `pull_changes(cursor=None)` after a rebuild may produce duplicate pushes if the backend has changed since the last actual sync. Documented as expected behaviour after `rmsync rescan`.
- Tombstones become a separate operational concern (backup, migrate). Documented in `docs/install.md`.
- Conflicts table loses its history on rebuild. Acceptable — conflicts are operational events, not long-lived state.

### Neutral

- We still use SQLite WAL mode, just for ordinary write durability, not for protocol-level two-phase commit.

## References

- ADR-0001 (cloud-layer pivot, which made this simplification possible)
- `docs/spec.md` §7 (state store schema)
