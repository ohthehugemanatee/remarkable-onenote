# ADR-0005: Representation archive format on file-storage backends

Status: accepted (2026-05-21)

## Context

On file-storage backends (NextCloud, OneDrive, future LocalDevice), rmsync must ship a representation of each tablet document that:

1. Preserves pen strokes losslessly so future tooling can re-render at higher fidelity.
2. Is immediately readable on devices the user already has (phone, laptop, browser) without rmsync-specific tooling.
3. Embeds enough metadata to recover the chain of provenance back to the rmfakecloud source.

A single PDF is readable everywhere but throws away stroke data. A raw `.rm` is faithful but unreadable. An SVG is half-readable and lossy on pressure/tilt rendering. None alone is sufficient.

## Decision

Each document is materialised as a folder on the backend:

```
<backend>/<routed-folder>/<doc-title>/
  ├── strokes.rm        # original from rmfakecloud, byte-equal
  ├── strokes.svg       # vector render via remarks
  ├── document.pdf      # paginated PDF; P3 adds embedded OCR text layer
  └── manifest.json     # render version, source SHA-256s, generation, page count
```

`strokes.rm`:
- Single-page documents: the raw `.rm` file bytes from rmfakecloud, no transformation.
- Multi-page documents: a `tar` archive (uncompressed) of the per-page `.rm` files in page order, with members named `<page-index>-<page-id>.rm`. The tar format is chosen for its triviality and universal tooling; uncompressed because the underlying `.rm` is already binary and gzip wins little.

`strokes.svg`: a single SVG with one `<g id="page-N">` per source page; stroke attributes preserve pressure (`stroke-width` interpolated per segment) and tilt where the source records it.

`document.pdf`: A5-portrait by default (matching the rM2's aspect ratio); page size configurable per-backend rule. Embeds the SVG as vectors, NOT a rasterised image.

`manifest.json`: schema defined in `docs/spec.md` §4. Contains SHA-256 of source files, `render_version` string, page count, rendered-at timestamp.

The folder name is the document's title with reMarkable-side path-illegal characters replaced (NUL, `/`, leading `.`, trailing whitespace). Title collisions are resolved by appending ` (rmsync-<short-doc-id>)`.

## Consequences

### Positive

- `strokes.rm` is the archival source of truth — losslessly preserved, byte-equal verifiable.
- `document.pdf` is immediately useful on every device with a PDF reader.
- `strokes.svg` is useful for the web (embeds nicely, scales) and as a debugging aid (open in Inkscape to inspect strokes).
- `manifest.json` enables programmatic re-rendering and provenance audits.
- Future tooling can re-render from `strokes.rm` when `remarks` improves, regenerating better-looking PDFs without losing fidelity.

### Negative

- Four files per document inflates the backend's file count. On a multi-thousand-document tablet this matters for backends with per-folder rate limits (OneDrive: ~5000 items per folder before listing degrades). Mitigated by the per-document folder structure pushing the limit out one level.
- Operators viewing the backend folder see a "folder per document" instead of a flat list. Documented; the trade-off is intentional.
- `strokes.rm` as a tar wrapper for multi-page documents is an rmsync convention, not an upstream format. Documented in `manifest.json` (`source.strokes_format: "raw" | "tar"`).

### Neutral

- Folder-per-document means we can add files later (HWR text dump, alt renders) without restructuring.

## Considered alternatives

- **Single `.rmdoc` file (the rM ecosystem's own format).** Considered. Less readable on non-rM devices (browsers, phones can't open it natively). Rejected for the "immediately useful" requirement.
- **PDF with `.rm` embedded as an attachment.** Considered. PDF readers don't surface attachments uniformly; many strip them. Lossy in practice. Rejected.
- **Per-page folders.** Considered. Inflates file count further with no obvious gain. Rejected.

## References

- `docs/spec.md` §4 (representation archive)
- `docs/spec.md` §6 (render pipeline)
- remarks: https://github.com/lucasrla/remarks
- rmscene: https://github.com/ricklupton/rmscene
