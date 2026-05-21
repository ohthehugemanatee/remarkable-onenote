# ADR-0004: rM-wins as the default conflict policy

Status: accepted (2026-05-21)

## Context

When the same document changes on both the tablet and a backend between sync cycles, rmsync must decide what to keep. There is no general right answer — different users with different workflows want different defaults. The decision must be: pick a sensible default, make alternatives easy.

## Decision

Default conflict policy is `rm_wins`:

- The tablet's version is preserved on the tablet.
- The backend's diverged version is written to a `/Conflicts/` folder on that backend, suffixed `(<backend> copy)`. The pre-divergence tablet version on the backend is suffixed `(rM original)` and remains in place.
- A `conflicts` row records the event for operator review.

Alternatives configurable per-rule:

- `branch` — keep both on both sides; doubles document count on every conflict.
- `manual` — pause sync for that `(doc, backend)` pair until operator runs `rmsync conflicts resolve`.

Rationale: the reMarkable is a writing-focused device. The most common conflict scenario is "user wrote on the tablet, file on the backend was edited by another tool / another person / the user from a different device." In that scenario, the user's *handwriting on the tablet* is the higher-value artefact because handwriting is harder to redo than text edits, and the tablet is the user's primary capture surface. `rm_wins` defaults to preserving that artefact.

`branch` is offered for users who collaborate heavily on the backend and want both versions visible without operator intervention.

## Consequences

### Positive

- Sensible default for the target workflow (handwriting-first capture).
- Backend-side divergent content is never lost — it lives in `/Conflicts/`, the operator can merge manually.
- Per-rule configurability means power users can mix policies.

### Negative

- Operators who edit primarily on the backend (e.g. Markdown sources in NextCloud) may be surprised by `rm_wins`. Documented prominently in `docs/install.md` and the status UI.
- `/Conflicts/` folders can accumulate cruft. The status UI surfaces conflict count; operator must periodically review.
- Hash-equal-but-rendered-differently scenarios (impossible in v1 because `strokes.rm` is byte-equal; possible in P3 once HWR text layers are embedded) need re-examination at that phase boundary.

### Neutral

- The conflict-detection mechanism (sync anchor on `last_known_remote_hash`) is independent of policy choice.

## References

- `docs/spec.md` §9 (conflict resolution)
