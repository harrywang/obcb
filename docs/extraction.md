# PDF extractors

[← README](../README.md)

The paper renders every PDF with olmOCR — a 7B vision-language model on a GPU — because its
corpus spans many publishers and includes scanned pages and exhibit-heavy layouts. That is the
main reason the reference pipeline is containerised. We make the extractor a **choice** instead,
so you get comparable structure without the GPU.

This matters more than it sounds. Case exhibits are financial statements, and a collapsed table
turns a numerical question into a guessing game. On our own data, the naive text layer returns
`Contribution Margin $6,950 $11,250 $3,800 $2,650 $3 ,750` — five columns fused, digits split.
A structure-aware extractor returns a real markdown table with the header row intact.

| Extractor | Kind | Tables | OCR | Licence | Install |
|---|---|---|---|---|---|
| **`pymupdf4llm`** | local | ✓ | ✗ | AGPL-3.0 | **default, base install** |
| `pypdf` | local | ✗ | ✗ | BSD | base install |
| `docling` | local | ✓ | opt-in | MIT | `uv sync --extra docling` |
| `llamaparse` | api | ✓ | ✓ | hosted, ~1000 free pages/mo | `uv sync --extra llamaparse` |
| `landingai` | api | ✓ | ✓ | hosted, metered | `uv sync --extra landingai` |

```bash
uv run obcb extract                          # auto: pymupdf4llm -> docling -> pypdf
uv run obcb extract --extractor docling      # MIT-licensed local
uv run obcb extract --extractor llamaparse   # hosted, for scans
uv run obcb extractors                         # see which are installed and ready
```

`pymupdf4llm` is the default: fastest, most accurate on our born-digital cases, and no model
download. It is AGPL-3.0 — swap in `docling` (MIT) via `OBCB_EXTRACTOR=docling` if you ever need
a permissive licence, at ~25s per 16-page document.

Reach for an **API extractor only when a local one comes back thin**. `extract` prints a warning
with the chars-per-page count when that happens, which is the signal that a document is scanned
rather than born-digital.

Two `pymupdf4llm` quirks are handled for you: MuPDF's malformed-XObject complaints are silenced,
and "picture text" blocks are stripped. The latter matters — text overlapping an image is
emitted a second time, which on watermarked pages duplicates the whole page and over exhibits
produces a scrambled restatement of numbers the real table already holds correctly. Set
`OBCB_KEEP_PICTURE_TEXT=true` to retain them.

## The OCR gap

Both local extractors read a text layer; neither reads a scan. For scanned cases, use
`--extractor llamaparse` or `--extractor landingai`, turn on `OBCB_DOCLING_OCR=true`, or fall
back to `reference-paper-code/modal_ocr/` for olmOCR itself.
