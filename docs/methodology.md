# Implementation methodology

Normative for every PR touching `src/rmsync/core/`, `src/rmsync/plugins/`, `src/rmsync/render/`, `src/rmsync/watcher/`, `src/rmsync/router/`, or `src/rmsync/inbound/`. Non-normative (encouraged) for the rest of the tree.

## The loop

1. **Red.** Failing tests committed first. The PR description names the critic-checklist categories (§2) the tests exercise. A PR that does not start with a red test is sent back without further review.
2. **Implementer.** Green plus refactor. No new production code lands without a red test that drives it.
3. **Adversarial critic.** A separate review pass — different context window, no access to the implementer's reasoning. The critic attacks the PR using the checklist (§2) and classifies findings as BLOCKER / MAJOR / MINOR.
4. **Re-implementer.** Fixes every BLOCKER and MAJOR. MINOR may defer with a tracking issue.
5. Goto 3 until the critic returns zero BLOCKER and zero MAJOR.

The critic is allowed (and expected) to write new failing tests against the implementer's code. Those tests become part of the PR.

## Critic checklist

The critic considers every category that touches the PR's change set. Categories the PR doesn't touch can be skipped; categories the PR's tests don't cover but the change *could* affect MUST be exercised by the critic.

### 2.1 rmfakecloud store atomicity

- Mid-write read: critic injects a read between metadata and `.rm` file writes; the watcher MUST detect the inconsistency and retry, not emit a partial event.
- Partial generation: metadata present without `.content`, or `.content` referencing pages whose `.rm` files don't exist.
- Atomic rename races: rmfakecloud's `<uuid>.metadata.new` → `<uuid>.metadata` rename observed mid-step.
- Settle-window starvation: continuous writes to a document MUST eventually be processed (not held forever by debouncing).

### 2.2 `.rm` parser version skew

- v6 and v7 line files in the same document.
- Pages with no strokes.
- Pages with strokes referencing layers not in the page list.
- Pages with empty strokes (zero-length).
- Future version bytes the parser doesn't recognise: archived verbatim, render fails gracefully, NOT crashes.

### 2.3 Conflict matrix

Cross product of: `{rm_moved, rm_edited, rm_deleted, rm_unchanged}` × `{remote_moved, remote_edited, remote_deleted, remote_unchanged}` × `{rm_wins, branch, manual}`. The critic picks at least four cells per PR that the implementer didn't.

### 2.4 Rule overlap and rule exit

- Document matches >1 rule under `union` with one `sync` and one `push` to different backends.
- Document matches >1 rule under `union` with two `sync` backends and identical `sync_priority` (must be rejected at config-load).
- Document moves *out* of a matching folder under each `on_rule_exit` policy.
- Document moves *between* two matching folders that route to different backends.

### 2.5 Idempotency

- Every backend op replayed: push the same `DocumentBundle` twice, expect identical end state.
- Cursor replay: pull with the same cursor twice, expect no duplicate inbound writes.
- Watcher replay after restart: events observed before crash and re-observed after restart MUST converge to the same state.
- Render replay: re-running the render pipeline on unchanged input produces byte-equal outputs (deterministic).

### 2.6 Identity stability

- Rename + immediate edit: tablet renames a document and edits it before the next pull; backend MUST see one document with new title and new content, not two.
- Move across rule boundary: routing changes; existing backend copy MUST be moved according to `on_rule_exit`, new backend MUST receive the document.
- Local rename on backend (operator renames in NextCloud Web UI): rmsync MUST track the rename and not duplicate.

### 2.7 Mirror-pull tombstone semantics

- Tablet-side delete followed by source-side delete → tombstone GC'd.
- Tablet-side delete followed by source-side content change → `tombstoned_source_changed` event, no resurrection.
- Tablet-side delete followed by source-side move → treated as content change (path changes are content for tombstone purposes).
- GC confirmation count requires authoritative `Gone`, not `Unknown` / timeout.

### 2.8 Backend move vs delete+create fallback

- Backend declares `supports_move: false` → rmsync MUST emit delete-then-create; representation archive at the new path MUST contain the same `strokes.rm` bytes.
- Backend declares `supports_move: true` but the specific move fails (cross-volume, permission, etc.) → fallback to delete+create with operator-visible warning.

### 2.9 Stroke preservation guarantee

The representation archive `strokes.rm` MUST be byte-equal to what rmsync read from rmfakecloud. Re-rendering MUST NOT mutate `strokes.rm`. Backends MUST NOT transform `strokes.rm` (no re-compression, no normalization).

### 2.10 Inbound-write boundary

rmsync MUST write into rmfakecloud only via its tablet-facing HTTP API. Critic tests assert no `open()` calls under `RMSYNC_RMFAKECLOUD_STORE` with a write flag.

### 2.11 CONFIG-ERROR format

Every config-load error MUST match `CE-<DOMAIN>-<NNN>: <cause> | hint: <action> | doc: <anchor>`. Critic asserts the format and that `<anchor>` resolves to a real section.

### 2.12 State-store rebuild

`rmsync rescan` MUST converge to a usable state DB from an empty start. Critic deletes `state.db` and asserts the next watcher cycle restores all `documents` and `representations` rows.

## Pull request checklist (mechanical)

Every PR description includes:

- [ ] Red test committed first; commit SHA referenced.
- [ ] Critic-checklist categories exercised.
- [ ] No production code without a red test.
- [ ] `ruff`, `mypy --strict`, `pytest` all green in CI.
- [ ] No emojis added to code or docs.
- [ ] Spec change? — also updates `docs/spec.md`.
- [ ] Behaviour change visible to operators? — also updates `docs/install.md` or relevant `docs/roadmap/*`.

## When the critic disagrees with the spec

The spec is the contract. If the critic finds a case the spec doesn't cover and the implementer's behaviour is defensible, the spec is amended in the same PR. If the implementer's behaviour is wrong, the implementation changes.

If the critic finds a case the spec contradicts itself on, work pauses until the contradiction is resolved at the spec level.
