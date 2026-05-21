# Roadmap: OneNote backend (P4)

Status: design sketch. Not implemented in v1. See [ADR-0006](../adr/0006-onenote-deferred-needs-office-addin.md) for the deferral rationale.

## Goal

A OneNote backend that ships tablet documents to OneNote with **stroke fidelity** — real ink, not raster — while remaining useful in degraded mode (text + image) when the stroke path is unavailable.

## Architecture (two surfaces)

OneNote has two distinct programmable surfaces with different capabilities:

| Surface | Runs where | Stroke API | Reach |
|---|---|---|---|
| Microsoft Graph (REST) | External server | None (HTML only) | Server-driven, always-on |
| OneNote JavaScript API (Office Add-in) | Inside OneNote client | Full `Ink`/`InkStroke`/`InkAnalysis` | User-driven, runs when OneNote is open with add-in active |

rmsync uses **both**.

### Component 1: Graph backend (Python, lives in `src/rmsync/plugins/onenote.py`)

Standard `BackendPlugin` implementation:

- `push(doc)` — creates the OneNote page via Graph: HTML scaffold, OCR'd text (from P3 HWR pipeline) for searchability, embedded image as visual fallback. Records a "needs stroke upgrade" job in the state DB.
- `pull_changes(cursor)` — Graph delta on the section the user has rmsync paired with.
- `move(doc_id, new_parent)` — Graph section-move; cross-notebook moves are delete+recreate (Graph doesn't support cross-notebook page moves).
- `lookup(doc_id)` — Graph page GET; returns `Present(hash)` based on OneNote's `lastModifiedDateTime`.
- Capabilities: `preserves_strokes=True (conditional)`, `supports_move=True`, `supports_pull=True`, `identity_stable=True`.

### Component 2: Office Add-in (TypeScript, lives in `addins/onenote/`)

A small Office Add-in that the user sideloads into OneNote desktop / Mac / iPad / Web. The add-in:

- Connects to rmsync over HTTPS using a per-add-in pairing token issued from `/ui/onenote-addin`.
- Sends a heartbeat every 60s while OneNote is open and the add-in pane is visible. rmsync records `last_addin_heartbeat` per OneNote account.
- Polls rmsync for queued "place these strokes on this page" jobs.
- For each job, retrieves the `.rm` stroke data from rmsync (rmsync converts `.rm` strokes to the JS API's `Ink` representation server-side; the add-in receives ready-to-write ink objects).
- Calls `OneNote.Application.getActiveOutline().paragraphs.addInk(...)` (or the equivalent for the page-level surface) to place the ink.
- Reports success back to rmsync, which marks the job complete.

### Behaviour matrix

| State | Push result |
|---|---|
| Add-in installed and heartbeat fresh (<30 days) | Graph creates page with text + image; add-in upgrades to real ink on next OneNote open |
| Add-in installed but stale heartbeat | Graph creates page with text + image; warning event surfaced in `/ui`; ink upgrade pending |
| No add-in ever paired | Graph creates page with text + image; `preserves_strokes` warning persistent in `/ui` |

## Distribution

- **Sideload manifest** shipped in `addins/onenote/manifest.xml`, with install instructions in `docs/install-onenote-addin.md` (TBD).
- **AppSource publication** — not a v1 self-hosted goal. Reconsidered if user base grows.

## Open questions

- **iPad add-in support.** OneNote iPad has the JS API but with restrictions. Per-page ink injection on iPad needs prototyping.
- **OneNote for Windows 10 (deprecated UWP).** Different API surface; not supported.
- **Stroke conversion fidelity.** `.rm` records pressure, tilt, and a custom curve format. The JS API's `InkStroke` accepts a points-with-pressure array. Direct conversion drops tilt. Acceptable as v1 of the add-in; revisit if users care.
- **Connectivity from add-in to rmsync.** If the user's OneNote is on a corporate laptop and rmsync is on a private home network, the add-in cannot reach rmsync. Options: (a) require rmsync to be reachable on the same network the add-in runs on, (b) introduce a hosted relay, (c) tunnel via the user's existing tools (Tailscale, etc.). Default: document option (a); options (b) and (c) are user choices.

## Out of scope for P4

- Bidirectional ink sync (user edits ink in OneNote, syncs back to tablet). The JS API can *read* ink strokes, so this is feasible — but the conversion path (JS API ink → `.rm` v6 binary) requires writing an `.rm` encoder, which rmscene doesn't currently expose. Deferred to a hypothetical P6.
- AppSource publication.
- Per-page granular sync (entire-page replace only).
