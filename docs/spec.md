# rmsync v1 specification

Status: draft. Source of truth for the implementation. All behaviour described as "MUST" is normative; behaviour described as "SHOULD" is the recommended default and may be overridden in configuration. Behaviour described as "MAY" is optional.

## 1. Scope and non-goals

### 1.1 Scope

rmsync is an integration layer that mirrors reMarkable tablet content out to configurable external backends (NextCloud, OneDrive in v1; OneNote in v4), and can pull selected content from those backends back onto the tablet. It runs as a server-side service, not on the tablet.

rmsync is **not** a reMarkable cloud replacement. The cloud-protocol layer is provided by [rmfakecloud](https://github.com/ddvk/rmfakecloud), which is a hard runtime dependency. rmsync sits *above* rmfakecloud, watching its filesystem store and writing back through its tablet-facing HTTP API.

### 1.2 Non-goals

- Implementing the reMarkable 15+15 sync protocol, root-hash bookkeeping, blob storage, or pairing flow. These are rmfakecloud's responsibility.
- Modifying `xochitl.conf` on the tablet, installing a CA on the tablet, or any other tablet-side configuration. Those are documented as user operations against rmfakecloud, not rmsync.
- Per-firmware compatibility tracking for the rM cloud protocol. rmsync depends on rmfakecloud's compatibility surface; firmware-specific protocol breakage is a bug to fix upstream in rmfakecloud, not in rmsync.
- Handwriting recognition (HWR) in v1. The plug-point lands in P1, the engine ships in P3.
- Editing OneNote pages in v1. OneNote ships in P4, requires a paired Office Add-in for stroke fidelity.

## 2. Architecture

### 2.1 Topology

```
┌──────────────┐    rM 15+15 sync     ┌──────────────────────┐
│  reMarkable  │ ───────────────────▶ │     rmfakecloud      │
│    tablet    │ ◀─────────────────── │   (Go, AGPL-3.0)     │
└──────────────┘                       │  user-store on disk  │
                                       └──────────┬───────────┘
                                                  │ filesystem (inotify)
                                                  │ + tablet-facing HTTP
                                                  ▼
                                       ┌──────────────────────┐
                                       │       rmsync         │ ◀─── operator
                                       │  (Python 3.12+,      │      via /ui
                                       │   single container)  │
                                       └──────────┬───────────┘
                                                  │ plugin push/pull
                              ┌───────────────────┼───────────────────┐
                              ▼                   ▼                   ▼
                       ┌────────────┐      ┌────────────┐      ┌────────────┐
                       │ NextCloud  │      │  OneDrive  │      │  OneNote   │
                       │  (v1)      │      │  (v1)      │      │  (v4)      │
                       └────────────┘      └────────────┘      └────────────┘
```

### 2.2 Integration boundary with rmfakecloud

rmsync depends on exactly two surfaces of rmfakecloud:

1. **Filesystem layout** of rmfakecloud's user store. Default location `/data/rmfakecloud/users/<uid>/`. Files of interest per document `<uuid>`:
   - `<uuid>.metadata` (YAML) — title, type, parent, last modified, deleted flag, tags
   - `<uuid>.content` (JSON) — page list, layer information, schema version
   - `<uuid>/<page-id>.rm` (binary, v6/v7) — stroke data per page
   - `<uuid>.pdf` / `<uuid>.epub` (binary, optional) — imported source files
2. **Tablet-facing HTTP sync API** of rmfakecloud. Used by rmsync to write inbound documents (changes pulled from backends) into the tablet's view, posing as a sync client. Authentication via a dedicated rmfakecloud user account distinct from the tablet's own pairing.

rmsync MUST NOT depend on rmfakecloud's internal Go interfaces, build-time hooks, plugin systems, or any other surface that would couple it to a specific rmfakecloud version beyond its filesystem and HTTP contracts.

If a future rmfakecloud change breaks either of those two surfaces, rmsync is updated; if a future rmfakecloud bug prevents rmsync from functioning, the fix is contributed upstream rather than worked around.

### 2.3 Components

Single OCI container, Python 3.12+, FastAPI as the ASGI app. The HTTP surface is small (`/ui`, `/healthz`, `/readyz`, optional `/api/v1/*`); most work runs in background tasks coordinated by APScheduler.

Internal modules:

- `rmsync.watcher` — inotify-based watcher over the rmfakecloud user store, with settle-window debouncing and atomicity guards (§5)
- `rmsync.render` — `.rm` → SVG/PDF rendering pipeline (rmscene + remarks), produces a per-document `manifest.json` (§6)
- `rmsync.state` — SQLite (WAL mode) derived index over rmfakecloud's store (§7)
- `rmsync.router` — YAML-driven rule engine that maps a document event to a list of (backend, op) actions (§8)
- `rmsync.plugins` — entry-point-loaded `BackendPlugin` implementations (§9)
- `rmsync.inbound` — per-backend pull loop; writes inbound documents into rmfakecloud via its HTTP API (§10)
- `rmsync.scheduler` — APScheduler driving pull jobs, GC, and reconciliation
- `rmsync.web` — read-only status UI under `/ui` (§11)
- `rmsync.cli` — operator CLIs (§12)
- `rmsync.hwr` — HWR plug-point (P1) and engines (P3)

## 3. Backend plugin contract

### 3.1 ABC

```python
class BackendPlugin(abc.ABC):
    name: str  # entry-point name, also used in rules.yaml

    @property
    @abc.abstractmethod
    def capabilities(self) -> Capabilities: ...

    @abc.abstractmethod
    def push(self, doc: DocumentBundle) -> PushAck: ...

    @abc.abstractmethod
    def pull_changes(self, cursor: Cursor | None) -> PullResult: ...

    @abc.abstractmethod
    def move(self, doc_id: DocId, new_parent: BackendPath) -> MoveAck: ...

    @abc.abstractmethod
    def lookup(self, doc_id: DocId) -> Lookup:  # Gone | Unknown | Present(hash)
        ...

    def delete(self, doc_id: DocId) -> DeleteAck:
        """Default: archive into `<configured-archive-folder>/<doc_id>` if
        capabilities.supports_archive_on_delete, else hard delete."""
```

### 3.2 Capabilities

```python
@dataclass(frozen=True)
class Capabilities:
    preserves_strokes: bool       # archive includes byte-equal strokes.rm?
    supports_move: bool           # backend has a move op; else delete+create
    supports_pull: bool           # backend has change feed (sync/mirror_pull)
    identity_stable: bool         # doc_id stable across rename/move on backend
    supports_archive_on_delete: bool
```

### 3.3 Discovery

Plugins register via Python entry points under `rmsync.backends`:

```toml
[project.entry-points."rmsync.backends"]
nextcloud = "rmsync.plugins.nextcloud:NextCloudBackend"
onedrive  = "rmsync.plugins.onedrive:OneDriveBackend"
null      = "rmsync.plugins.null:NullBackend"
```

Third-party backends ship as separate distributions exposing the same entry-point group.

### 3.4 Contract tests

A contract test harness (`tests/contract/test_backend_contract.py`) is parametrized over all installed entry points. It MUST cover at minimum: push idempotency under retry, pull cursor replay, move (or delete+create fallback) preserving the representation archive, lookup returning each of `Gone | Unknown | Present(hash)`, capability flags self-consistent (e.g. `direction: sync` requires `identity_stable`).

## 4. Representation archive

For file-storage backends (NextCloud, OneDrive), each document is materialized as a folder:

```
<backend>/<routed-folder>/<doc-title>/
  ├── strokes.rm        # original from rmfakecloud, byte-equal
  ├── strokes.svg       # vector render via remarks
  ├── document.pdf      # paginated PDF; P3 adds embedded OCR text layer
  └── manifest.json     # render version, source SHA-256s, generation, page count
```

`strokes.rm` is the archival source of truth — it MUST be byte-equal to the file rmsync read from rmfakecloud's store. Re-rendering at a future date (e.g. when `remarks` improves) operates from `strokes.rm`, not the rendered outputs.

`manifest.json` schema:

```json
{
  "rmsync_version": "1.0.0",
  "render_version": "remarks-0.3.4+rmscene-0.6.1",
  "source": {
    "metadata_sha256": "...",
    "content_sha256": "...",
    "strokes_sha256": "...",
    "rmfakecloud_doc_id": "<uuid>",
    "rmfakecloud_generation": 42
  },
  "pages": 3,
  "rendered_at": "2026-05-21T20:04:00Z"
}
```

## 5. Watcher and atomicity

### 5.1 Inotify

The watcher subscribes to `IN_CLOSE_WRITE | IN_MOVED_TO | IN_DELETE | IN_MOVED_FROM` on the rmfakecloud user store root and its subdirectories (recursive via per-directory watches; symlinks not followed).

### 5.2 Settle window

rmfakecloud's sync daemon writes documents in multiple file operations (metadata, content, per-page `.rm` files), not atomically as a whole document. The watcher MUST debounce events per `(uid, doc_id)` with a configurable settle window (default 2 seconds). A document is only processed once the window has elapsed since the last file event for it.

### 5.3 Atomicity guards

Before processing, the watcher MUST:

1. Take an advisory `flock` on the document's directory (best-effort; rmfakecloud may not cooperate).
2. Read the `.metadata` and `.content` files.
3. Verify the page list in `.content` matches the `.rm` files present on disk.
4. Re-read the `.metadata` and confirm its SHA-256 has not changed since step 2.

If any check fails, the watcher releases the lock, increments a per-document retry counter, and re-enqueues with exponential backoff (max 5 attempts, then logged as `watcher_atomicity_exhausted` and surfaced in the status UI).

### 5.4 Cold-start scan

On startup the watcher MUST perform a full scan of the user store, reconcile against the state DB, and emit synthetic events for any document where the on-disk state differs from the DB. This makes the DB recoverable by deletion-and-rescan.

## 6. Render pipeline

### 6.1 Inputs

A `DocumentBundle` consisting of: `metadata` (parsed YAML), `content` (parsed JSON), and a list of `Page(page_id, rm_bytes)`.

### 6.2 Outputs

- `strokes.rm` — concatenation/archive of source `.rm` files in page order. v1 archives per-page files inside a tarball when there is more than one page; single-page documents archive the raw `.rm`. (ADR-0005 details the layout choice.)
- `strokes.svg` — single SVG with one `<g>` element per page, produced by `remarks`. Stroke attributes preserve pressure/tilt where the source `.rm` records them.
- `document.pdf` — paginated PDF, one page per source page, A5-portrait by default (configurable per-backend), produced by `remarks`.
- `manifest.json` — §4.

### 6.3 Versioning

`render_version` in `manifest.json` MUST change whenever the output bytes for a given input would change. On version mismatch, the rendering is considered stale and re-rendered on the next event for that document.

### 6.4 Failures

Render failures MUST NOT block the watcher from observing further events. A failed render leaves the document in state `render_failed` in the state DB, surfaces in the status UI, and is retried by the scheduler with exponential backoff up to 1h interval. `strokes.rm` is still archived even when rendering fails — fidelity preservation takes priority over readable output.

## 7. State store

SQLite, WAL mode, default path `/var/lib/rmsync/state.db`. The DB is a derived index — it MAY be deleted at any time and rebuilt by cold-start scan (§5.4) + re-pulling backend cursors (§10).

### 7.1 Tables

```sql
documents (
  doc_id              TEXT PRIMARY KEY,        -- rmfakecloud uuid
  title               TEXT NOT NULL,
  parent_doc_id       TEXT,                    -- FK documents.doc_id (folder)
  type                TEXT NOT NULL,           -- notebook | pdf | epub | folder
  rm_generation       INTEGER NOT NULL,
  metadata_sha256     TEXT NOT NULL,
  content_sha256      TEXT,
  strokes_sha256      TEXT,
  state               TEXT NOT NULL,           -- pending | rendered | render_failed
  last_event_at       INTEGER NOT NULL,        -- unix ms
  deleted_at          INTEGER                  -- unix ms; NULL if not deleted
)

representations (
  doc_id              TEXT NOT NULL REFERENCES documents,
  render_version      TEXT NOT NULL,
  archive_path        TEXT NOT NULL,           -- rmsync-local cache path
  manifest_sha256     TEXT NOT NULL,
  PRIMARY KEY (doc_id, render_version)
)

routing_decisions (
  doc_id              TEXT NOT NULL REFERENCES documents,
  rule_id             TEXT NOT NULL,           -- stable hash of rule body
  backend             TEXT NOT NULL,
  direction           TEXT NOT NULL,
  decided_at          INTEGER NOT NULL,
  PRIMARY KEY (doc_id, backend)
)

backend_state (
  doc_id              TEXT NOT NULL REFERENCES documents,
  backend             TEXT NOT NULL,
  remote_id           TEXT,                    -- backend-side identity
  remote_parent       TEXT,
  remote_hash         TEXT,                    -- backend's notion of content hash
  last_pushed_at      INTEGER,
  last_pulled_at      INTEGER,
  last_known_remote_hash TEXT,                 -- sync-anchor for conflict detection
  PRIMARY KEY (doc_id, backend)
)

move_journal (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id              TEXT NOT NULL,
  from_parent         TEXT,
  to_parent           TEXT,
  source              TEXT NOT NULL,           -- rm | <backend>
  observed_at         INTEGER NOT NULL,
  applied_at          INTEGER                  -- NULL until propagated
)

conflicts (
  doc_id              TEXT NOT NULL,
  backend             TEXT NOT NULL,
  policy              TEXT NOT NULL,           -- rm_wins | branch | manual
  rm_hash             TEXT NOT NULL,
  backend_hash        TEXT NOT NULL,
  detected_at         INTEGER NOT NULL,
  resolved_at         INTEGER,
  resolution          TEXT,                    -- kept_rm | kept_backend | branched
  PRIMARY KEY (doc_id, backend, detected_at)
)

backend_cursors (
  backend             TEXT PRIMARY KEY,
  cursor              TEXT,                    -- opaque, backend-defined
  last_pulled_at      INTEGER
)

mirror_pull_tombstones (
  doc_id              TEXT NOT NULL,
  backend             TEXT NOT NULL,
  last_seen_source_hash TEXT NOT NULL,
  gc_confirm_count    INTEGER NOT NULL DEFAULT 0,
  gc_first_confirm_at INTEGER,
  created_at          INTEGER NOT NULL,
  PRIMARY KEY (doc_id, backend)
)
```

### 7.2 Rebuild

`rmsync rescan` performs a cold-start scan (§5.4) and re-derives `documents` + `representations` from rmfakecloud's store. `routing_decisions` are recomputed by replaying the current `rules.yaml` against each document. `backend_state` and `backend_cursors` are NOT rebuilt — they reflect remote truth and require a fresh pull cycle to repopulate (lossy: existing remote content is treated as new, which MAY cause duplicate pushes; this is documented as expected behaviour after a rebuild).

## 8. Selective sync rules

### 8.1 Config file

Default location `/etc/rmsync/rules.yaml`. Hot-reloaded on file change with validation. Invalid config keeps the previous good config running and surfaces the error in logs and the status UI.

```yaml
rules:
  - id: work                                    # optional stable id; auto-generated if absent
    match:
      folder: "/Work"                           # glob-pattern, leading slash anchors to root
      type: notebook                            # optional; any of notebook|pdf|epub|*
    backends:
      - name: nextcloud
        direction: sync                         # push | mirror_pull | sync | archive
        sync_priority: 10                       # required when rule_overlap=union and >1 sync backend on same path-set
  - id: personal-cloud
    match:
      tag: personal
    backends:
      - name: onedrive
        direction: push
  - id: inbox
    match:
      folder: "/Inbox"
    backends:
      - name: nextcloud
        direction: mirror_pull
        target_folder: "/rM-Inbox"              # required for mirror_pull

on_rule_exit: archive                           # archive | delete | move_to_archive_folder
rule_overlap: union                             # union | first_match | error

config_version: 1
```

### 8.2 Validation

Config-load MUST enforce:

- **CE-RULES-001**: `direction: sync` is rejected if the named backend's `capabilities.identity_stable` is false.
- **CE-RULES-002**: `rule_overlap: union` with multiple `sync` backends covering overlapping paths requires every such rule to declare `sync_priority`.
- **CE-RULES-003**: A path used as any rule's `mirror_pull.target_folder` cannot also appear as `match.folder` in any `push | sync | archive` rule.
- **CE-RULES-004**: Backend names MUST resolve to a loaded plugin entry point.
- **CE-RULES-005**: `match` must contain at least one of `folder | tag | type | title_regex`.

Errors use the format `CE-<DOMAIN>-<NNN>: <cause> | hint: <action> | doc: <anchor>` where `<anchor>` references the relevant section of this spec or the install doc.

### 8.3 Match evaluation

For a document, the router computes the set of matching rules. Under `rule_overlap: union`, the union of `(backend, direction)` tuples from all matching rules applies; collisions on the same backend MUST be resolved by `sync_priority` (highest wins). Under `first_match`, only the first rule (in YAML order) applies. Under `error`, a document matching >1 rule causes the document to be flagged `routing_error` in the state DB and surfaced in the UI, but does not affect other documents.

### 8.4 Rule exit

When a document no longer matches a rule that previously routed it to a backend, `on_rule_exit` determines fate of the existing backend copy: `archive` (leave it where it is, mark `routing_decisions.direction = orphaned`), `delete` (push a delete op), or `move_to_archive_folder` (move to a configurable per-backend archive path).

## 9. Conflict resolution

### 9.1 Detection

A conflict exists when `backend_state.last_known_remote_hash != current remote hash` AND `documents.strokes_sha256` has changed since `backend_state.last_pushed_at`. Both sides changed since the last sync anchor.

### 9.2 Default policy: `rm_wins`

The tablet version is preserved on the tablet. The backend version is written to a per-backend `/Conflicts/` folder with suffixes `(rM original)` (the tablet's current view) and `(<backend> copy)` (the diverged backend version). A `conflicts` row is recorded; the operator may manually merge or accept one side.

### 9.3 Alternative policy: `branch`

Both sides are preserved on both sides: the backend gets `<title> (rM copy)` and `<title> (<backend> copy)`; the tablet gets two notebooks with the same suffixes via the inbound-write path. Use sparingly — it doubles the document count for every conflict.

### 9.4 Manual policy: `manual`

A conflict pauses sync for that `(doc, backend)` pair until the operator runs `rmsync conflicts resolve <doc_id> --keep rm|backend|branched`.

## 10. Inbound writes

### 10.1 Backend pull cursors

Each backend MUST expose `pull_changes(cursor)` returning a `PullResult(changes: list[Change], next_cursor: Cursor)`. Cursors are opaque to rmsync and persisted in `backend_cursors`. The scheduler invokes `pull_changes` per backend on a configurable interval (default 5 minutes, per-backend overridable).

### 10.2 Writing into rmfakecloud

For a new document inbound from a backend with `direction in {mirror_pull, sync}`:

1. rmsync converts the backend representation to a `DocumentBundle` (best-effort: PDFs become PDF notebooks; non-rM-native formats become PDF-with-template attachments).
2. rmsync authenticates to rmfakecloud's tablet-facing HTTP API using a dedicated rmfakecloud user account configured at deploy time (separate from the tablet's pairing).
3. rmsync POSTs the bundle through rmfakecloud's sync flow as if it were a sync client. rmfakecloud then propagates to the tablet on the tablet's next sync.

rmsync MUST NOT write directly into rmfakecloud's filesystem store. Mutations only flow through the HTTP API to preserve rmfakecloud's invariants and avoid races with its own writes.

### 10.3 Mirror-pull tombstones

When a `mirror_pull` document is deleted on the tablet (observed via watcher), an entry is inserted into `mirror_pull_tombstones`. On subsequent pull cycles, any change to that `(doc_id, backend)` is suppressed:

- If the next `pull_changes` returns the document with the same hash as `last_seen_source_hash`, the tombstone is confirmed; `gc_confirm_count++` and `gc_first_confirm_at` is set if NULL.
- If `pull_changes` returns the document with a different hash, an event `tombstoned_source_changed` is emitted (logged + UI surface). No auto-resurrection. Operator clears the tombstone manually via `rmsync tombstones clear <doc_id> --backend <name>`.
- If `pull_changes` returns the document as `Gone` (authoritative `404`, not timeout/Unknown), `gc_confirm_count++`.
- After `gc_confirm_count >= 2` AND `now - gc_first_confirm_at >= 24h`, the tombstone is garbage-collected.

## 11. Status web UI

Read-only in v1. Mounted under `/ui`. HTTP Basic auth with a single configured operator credential; production deployments are expected to front this with their existing reverse-proxy auth (OIDC/forward-auth).

Pages:

- **`/ui/`** — overview: rules count, last config-load result, total documents, render-pipeline health, per-backend cursor lag
- **`/ui/rules`** — currently-loaded rules pretty-printed; validation errors if any
- **`/ui/events`** — paginated sync events (per-doc, per-backend, op, outcome, latency)
- **`/ui/conflicts`** — conflict queue with hash diffs; CLI commands to copy-paste for resolution (no write actions in v1)
- **`/ui/tombstones`** — tombstone list with `last_seen_source_hash` and GC countdown
- **`/ui/backends`** — per-backend health, cursor lag, pull-loop status, capability flags
- **`/ui/render`** — render-pipeline stats and recent failures

## 12. Operator CLIs

```
rmsync status                        # one-line health summary
rmsync rescan                        # cold-start scan, rebuild derived state
rmsync rules validate [<path>]       # check config without applying
rmsync rules suggest                 # emit a starter rules.yaml from current store
rmsync rules diff                    # show rules.yaml vs currently-loaded
rmsync tombstones list
rmsync tombstones clear <doc_id> --backend <name>
rmsync conflicts list
rmsync conflicts resolve <doc_id> --backend <name> --keep rm|backend|branched
rmsync replay <doc_id>               # force re-route + re-push of one document
rmsync backend <name> probe          # connectivity + capability self-test
```

All CLIs MUST emit machine-readable JSON when `--json` is passed.

## 13. Deployment

### 13.1 Single container

`docker run -v /data/rmfakecloud:/data/rmfakecloud:ro -v rmsync-state:/var/lib/rmsync -v rmsync-archive:/var/lib/rmsync/archive -p 8080:8080 ghcr.io/ohthehugemanatee/rmsync:latest`

The user store is mounted read-only — rmsync never writes to rmfakecloud's filesystem (§10.2).

### 13.2 docker-compose

`examples/docker-compose.yml` brings up rmfakecloud + rmsync + a Caddy front-end doing ACME-HTTP-01 against a domain the operator owns. This is the recommended path for self-hosted users.

### 13.3 Helm

`examples/helm/` packages the same stack for Kubernetes. Uses the cluster's default StorageClass; optional cert-manager integration for TLS. No assumptions about specific ingress controllers (Traefik, nginx, etc.) — the operator wires up their own.

### 13.4 Environment variables

- `RMSYNC_RULES_PATH` (default `/etc/rmsync/rules.yaml`)
- `RMSYNC_STATE_DB` (default `/var/lib/rmsync/state.db`)
- `RMSYNC_ARCHIVE_DIR` (default `/var/lib/rmsync/archive`)
- `RMSYNC_RMFAKECLOUD_STORE` (default `/data/rmfakecloud/users`)
- `RMSYNC_RMFAKECLOUD_API` (e.g. `https://rm.example.com`)
- `RMSYNC_RMFAKECLOUD_USER`, `RMSYNC_RMFAKECLOUD_PASSWORD` (dedicated account, §10.2)
- `RMSYNC_UI_USER`, `RMSYNC_UI_PASSWORD` (HTTP Basic for `/ui`)
- Per-backend credentials in `/etc/rmsync/secrets/<backend>.env` (mounted from a Secret in k8s deployments)

## 14. Observability

### 14.1 Health endpoints

- `/healthz` — process up, SQLite reachable, inotify subscribed
- `/readyz` — additionally: at least one backend plugin loaded, config valid, rmfakecloud API reachable

### 14.2 Metrics (P5)

Prometheus endpoint at `/metrics`. Key series:
- `rmsync_watcher_events_total{op=create|update|delete|move}`
- `rmsync_render_duration_seconds` histogram
- `rmsync_render_failures_total{reason=...}`
- `rmsync_push_duration_seconds{backend=...}` histogram
- `rmsync_pull_changes_total{backend=...}`
- `rmsync_conflicts_total{policy=...}`
- `rmsync_tombstones_active`
- `rmsync_backend_cursor_lag_seconds{backend=...}`

### 14.3 Structured logs

JSON-line logs to stdout with fields: `level`, `event`, `doc_id`, `backend`, `op`, `latency_ms`, `result`, `err_class`. No raw stroke data in logs.

## 15. Security

- rmfakecloud's user store is mounted read-only into rmsync.
- The dedicated rmfakecloud user account (§10.2) is the only path for rmsync mutations; its credentials are loaded from a Secret, never logged.
- The status UI defaults to HTTP Basic; deployments fronting with OIDC SHOULD bypass Basic auth via header forwarding.
- Backend credentials live in per-backend secret files, never in `rules.yaml`.
- No telemetry, no calls home, no third-party analytics. The only outbound traffic is to configured backends and rmfakecloud.

## 16. Phasing summary

See top-level plan for full phase definitions. Each phase has an explicit exit gate documented in the plan. The implementation MUST follow the adversarial-critic methodology described in `docs/methodology.md`.

## 17. Glossary

- **rmfakecloud** — the upstream self-hosted reMarkable cloud replacement that rmsync sits above.
- **DocumentBundle** — `(metadata, content, [pages])` read from rmfakecloud's store.
- **Representation archive** — the per-document folder containing `strokes.rm` + renders + manifest written to file-storage backends.
- **Direction** — per-rule per-backend sync mode: `push | mirror_pull | sync | archive`.
- **Sync anchor** — the `(rm_hash, remote_hash)` pair recorded in `backend_state` after a successful round-trip; conflict detection compares the current pair against this anchor.
- **Tombstone** — a record that a `mirror_pull` document was deleted on the tablet, preventing resurrection on the next pull cycle.
- **render_version** — opaque string identifying a deterministic rendering output; documents are re-rendered when their stored `render_version` differs from the current one.
