# Roadmap: Handwriting recognition (P3)

Status: design sketch. The plug-point ships in P1 (default no-op); engines ship in P3.

## Goal

Embed an OCR text layer into the `document.pdf` in each representation archive, so that backend-side full-text search (NextCloud `files_fulltextsearch_tesseract`, OneDrive native search) finds known phrases on handwritten pages.

The text layer is a PDF feature — invisible text positioned behind the rendered strokes. Search tools and screen readers see the text; the visual remains the handwriting.

## Plug-point (P1)

```python
class HWRBackend(abc.ABC):
    name: str

    @abc.abstractmethod
    def recognize(self, page: RenderedPage) -> RecognizedPage:
        """page contains stroke geometry + page-level raster.
        Return text + bounding boxes per word."""

    @property
    @abc.abstractmethod
    def capabilities(self) -> HWRCapabilities:
        ...

@dataclass(frozen=True)
class HWRCapabilities:
    offline: bool                  # runs without network
    languages: list[str]           # ISO-639 codes
    cost_per_page_usd: float       # 0 for offline
    typical_latency_ms: int        # rough order of magnitude
```

P1 ships a `null` HWR backend that returns an empty recognition for every page. The render pipeline calls `recognize()` and embeds whatever text it returns; with the null backend, no text is embedded.

## P3 engines

### Default: Kraken (offline, free)

- https://kraken.re/
- Python-native, pip-installable, model files bundled in the rmsync image (English by default; other languages loadable per-config).
- Pre-trained models for handwritten text.
- Typical latency: ~500ms/page on CPU, ~100ms/page with CUDA.
- No network required. Default choice for self-hosted-everything users.

### Alt: TrOCR (offline, free, GPU-friendly)

- https://huggingface.co/microsoft/trocr-base-handwritten
- Transformer-based, higher quality than Kraken on cursive in our spot-checks.
- Image size of ~3GB once weights are downloaded. Not bundled in the rmsync image; pulled at first run from a configurable model source.
- Typical latency: ~200ms/page on GPU, ~3s/page on CPU.

### Alt: Cloud APIs (paid, network)

Three configurable adapters:

- **Google Document AI** — `gcp_document_ai` plugin. Best quality for printed text mixed with handwriting; per-page pricing.
- **MyScript iink** — `myscript` plugin. Best quality for pure handwriting; specialised in the reMarkable use case (other tools in the rM ecosystem use it). Subscription model.
- **Microsoft Computer Vision Read** — `azure_read` plugin. Reasonable quality, integrates with Azure billing for users already there.

## Configuration

```yaml
# rules.yaml additions
hwr:
  engine: kraken                   # kraken | trocr | gcp_document_ai | myscript | azure_read | null
  language: en
  per_backend_override:
    onenote: null                  # OneNote backend uses its own InkAnalysis instead
```

OneNote (P4) overrides HWR to `null` because the OneNote JS API exposes `InkAnalysis` which produces OneNote-native HWR — superior because it integrates with OneNote's own search. rmsync's HWR pipeline is bypassed for that backend.

## Text-layer embedding

After `recognize()` returns words with bounding boxes per page, the render pipeline:

1. Opens `document.pdf` with `pikepdf`.
2. Per page, creates an invisible text object positioned at the recognised bounding boxes (font size 1pt, text rendering mode 3 = invisible).
3. Saves a new `document.pdf` over the old one.

The text layer is robust to all major PDF readers and search indexers. NextCloud's `files_fulltextsearch_tesseract` reads it directly; OneDrive's search reads it directly.

## Quality measurement

P3 ships a test corpus of handwritten pages with ground-truth transcripts. Each engine is benchmarked on word-level accuracy. Results published in `docs/hwr-benchmarks.md`. The default engine choice is reviewed if Kraken's accuracy drops below 75% on the corpus.

## Privacy considerations

Cloud HWR engines send the page raster (or stroke data) to a third party. The default `kraken` is offline and never exits the rmsync container. Operators selecting cloud engines opt into that data flow; the status UI surfaces a warning when a cloud engine is configured.

## Open questions

- **Per-page caching of recognition results.** Recognition is the slowest step in the render pipeline. Cache keyed on `strokes_sha256` to avoid re-recognising unchanged pages.
- **Streaming vs batch recognition.** Most engines have a single-page API; some (Google Document AI) support multi-page batches for cost efficiency. Per-engine adapter choice.
- **Multilingual documents.** Some engines support language auto-detection; others require per-page hints. Document this per adapter.
- **Math and diagrams.** HWR engines vary wildly on math notation. v3 punts; v4 may add a math-aware adapter.
