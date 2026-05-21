# ADR-0006: OneNote stroke fidelity requires an Office Add-in; OneNote deferred to P4

Status: accepted (2026-05-21)

## Context

The original project name (`remarkable-onenote`) and the first planning rounds put OneNote among the v1 backends. Investigation during the rmfakecloud pivot found that:

- **Microsoft Graph's OneNote REST API** (the surface available to external servers) only accepts HTML pages. It has no endpoint for posting ink strokes. Any image you embed becomes a raster, losing the vector strokes. There is no `InkML` upload, no proprietary stroke-XML endpoint.
- **The [OneNote JavaScript API for Office Add-ins](https://learn.microsoft.com/en-us/javascript/api/onenote)** (the surface available to code running *inside* OneNote as an add-in) exposes `Ink`, `InkStroke`, `InkWord`, `InkAnalysis` — full ink object model, read and write. The JS API runs on OneNote for Windows desktop, OneNote for Mac, OneNote for iPad (limited), and OneNote on the Web.

These are two different surfaces with different capabilities. Stroke fidelity is possible on OneNote, but only through the JS API path.

## Decision

OneNote is deferred from v1 to **P4** and the architecture is:

- **Graph REST** handles page lifecycle: notebook/section management, HTML scaffold creation with the OCR'd text from rmsync's HWR pipeline (P3), embedded image of the rendered page as a fallback visual.
- **An Office Add-in** (separate JavaScript/TypeScript project under `addins/onenote/` in the same repo) handles stroke injection via the OneNote JS API. The add-in is sideloaded into the user's OneNote, communicates with rmsync over HTTP, drains a queue of "place these strokes on this page" jobs when OneNote is open and the add-in is loaded.

Distribution: sideload manifest published in the repo, with operator-facing install instructions. AppSource publication is a non-goal for v1 self-hosted users.

Behaviour without the add-in:
- Pages still get created via Graph with searchable text + embedded raster.
- The next time the user opens OneNote with the add-in active, queued stroke jobs are drained and the page is *upgraded* in place — the embedded raster is replaced with real ink strokes.

This means OneNote's `preserves_strokes` capability is `true` *conditional* on the add-in being installed; rmsync's plugin reports it as `true` but emits a warning event when pushing to a OneNote account that has no add-in heartbeat in the last 30 days.

## Consequences

### Positive

- Stroke fidelity is achievable on OneNote, not lost.
- The HTML-via-Graph path means OneNote is useful even without the add-in (searchable text, embedded image). Strokes upgrade later.
- Two codebases isolate concerns: Python service side, JavaScript add-in side. Each can iterate independently.
- The pattern (Graph for lifecycle + add-in for ink) generalises to other Office-ink scenarios should they arise.

### Negative

- OneNote becomes a substantially larger piece of work than NextCloud or OneDrive: separate language, separate distribution, separate runtime environment. Justifies the deferral to P4.
- Stroke sync isn't truly server-driven on OneNote — it requires the user to open OneNote with the add-in active for queued jobs to drain. Degrades gracefully (text + image first, strokes later), but degrades.
- The add-in needs a connection to rmsync. For deployments where rmsync is on a private network and the user's OneNote is on a corporate-managed laptop, the connectivity path is non-trivial — documented as P4 ops concern.
- Manifest sideloading is a friction point for non-technical users. Mitigated by AppSource publication if user-base growth justifies the effort.

### Neutral

- The repo name `remarkable-onenote` becomes accurate again at P4, despite the v1 shipping non-OneNote backends first.

## Considered alternatives

- **OneNote as v1 backend, HTML-only.** Ships fast but every notebook becomes a flat raster — stroke fidelity lost permanently for OneNote users. Rejected on the stroke-preservation requirement.
- **OneNote via a third-party stroke library that recreates `.one` files directly.** Reverse-engineering OneNote's binary format. High effort, fragile across OneNote versions, no upstream support path. Rejected.
- **Skip OneNote permanently.** The user has explicit OneNote interest; the repo is even named for it. Rejected.

## References

- OneNote JavaScript API reference: https://learn.microsoft.com/en-us/javascript/api/onenote?view=onenote-js-1.1
- Microsoft Graph OneNote API: https://learn.microsoft.com/en-us/graph/api/resources/onenote-api-overview
- `docs/roadmap/onenote.md` (architecture sketch for P4)
- ADR-0001 (rmfakecloud pivot, which created the bandwidth to defer OneNote)
