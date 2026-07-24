"""Pluggable PDF -> markdown extractors.

The paper renders every PDF with olmOCR, a 7B vision-language model on a GPU, because
its corpus spans many publishers and includes scanned pages and exhibit-heavy layouts
where a plain text layer is useless. We keep that quality bar without the GPU by making
the extractor a choice rather than a hardcoded call.

Extractors fall into two families:

  local  - run on your machine, no network, no per-page cost
  api    - hosted "agentic" parsers, better on hard scans, metered

Every extractor returns markdown. Structure-aware extractors emit real markdown tables,
which is what matters here: our source cases carry financial exhibits, and a collapsed
table turns a numerical question into a guessing game.

Selection order for the default "auto": pymupdf4llm -> docling -> pypdf.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import config

# ---------------------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Extractor:
    name: str
    kind: str  # "local" or "api"
    tables: bool  # emits real markdown tables
    ocr: bool  # can read scanned / image-only pages
    install: str  # how to make it available
    env: str | None  # required API key, if any
    note: str


EXTRACTORS: dict[str, Extractor] = {
    "pypdf": Extractor(
        "pypdf", "local", False, False,
        "included in the base install", None,
        "Dependency-light fallback. Text layer only; collapses tables into ambiguous runs.",
    ),
    "pymupdf4llm": Extractor(
        "pymupdf4llm", "local", True, False,
        "included in the base install", None,
        "Default. Fast, accurate markdown tables, no model download. AGPL-3.0, "
        "which is why it ships as the default here.",
    ),
    "docling": Extractor(
        "docling", "local", True, False,
        "uv sync --extra docling", None,
        "IBM, MIT licensed. CPU table-structure model, ~25s for 16 pages, "
        "downloads weights on first use. The permissive choice.",
    ),
    "llamaparse": Extractor(
        "llamaparse", "api", True, True,
        "uv sync --extra llamaparse", "LLAMA_CLOUD_API_KEY",
        "LlamaIndex hosted agentic OCR. Free tier ~1000 pages/month.",
    ),
    "landingai": Extractor(
        "landingai", "api", True, True,
        "uv sync --extra landingai", "VISION_AGENT_API_KEY",
        "LandingAI Agentic Document Extraction (Andrew Ng). Reasons over layout, "
        "charts, and figures rather than just lifting text.",
    ),
}

LOCAL_PREFERENCE = ["pymupdf4llm", "docling", "pypdf"]


class ExtractorUnavailable(RuntimeError):
    pass


def _require(module: str, extractor: str) -> None:
    import importlib.util

    if importlib.util.find_spec(module) is None:
        b = EXTRACTORS[extractor]
        raise ExtractorUnavailable(f"extractor '{extractor}' needs `{module}`. Install it: {b.install}")


def _require_key(extractor: str) -> str:
    b = EXTRACTORS[extractor]
    assert b.env
    key = os.environ.get(b.env)
    if not key:
        raise ExtractorUnavailable(
            f"extractor '{extractor}' needs {b.env}. Add it to .env (see .env.example)."
        )
    return key


# ---------------------------------------------------------------------------------------
# Local extractors
# ---------------------------------------------------------------------------------------


def _pypdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


_PICTURE_TEXT = re.compile(
    r"<!-- Start of picture text -->.*?<!-- End of picture text -->", re.S
)


def _pymupdf4llm(path: Path) -> str:
    _require("pymupdf4llm", "pymupdf4llm")
    import pymupdf
    import pymupdf4llm

    # MuPDF writes malformed-XObject complaints straight to stderr on many publisher
    # PDFs. They are harmless and drown out our own progress output.
    pymupdf.TOOLS.mupdf_display_errors(False)

    markdown = pymupdf4llm.to_markdown(str(path), show_progress=False)

    # Text that overlaps an image comes back a second time inside a "picture text"
    # block. On watermarked pages that is the whole page duplicated, and over exhibit
    # tables it is a scrambled restatement of numbers the real markdown table already
    # holds correctly - actively harmful next to the clean version. Drop by default.
    if not config.KEEP_PICTURE_TEXT:
        markdown = _PICTURE_TEXT.sub("", markdown)
    return markdown


def _docling(path: Path) -> str:
    _require("docling", "docling")
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    # do_ocr=False keeps it fast on born-digital PDFs; flip it on for scans.
    opts = PdfPipelineOptions(
        do_ocr=config.DOCLING_OCR,
        do_table_structure=True,
    )
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    return converter.convert(str(path)).document.export_to_markdown()


# ---------------------------------------------------------------------------------------
# API extractors
# ---------------------------------------------------------------------------------------


def _llamaparse(path: Path) -> str:
    key = _require_key("llamaparse")
    try:  # current SDK
        from llama_cloud_services import LlamaParse
    except ImportError:
        try:  # older standalone package
            from llama_parse import LlamaParse  # type: ignore[no-redef]
        except ImportError as exc:
            raise ExtractorUnavailable(
                f"extractor 'llamaparse' needs the SDK. Install it: {EXTRACTORS['llamaparse'].install}"
            ) from exc

    parser = LlamaParse(api_key=key, result_type="markdown")
    docs = parser.load_data(str(path))
    return "\n\n".join(getattr(d, "text", "") or "" for d in docs)


def _landingai(path: Path) -> str:
    _require_key("landingai")
    try:  # current SDK
        from landingai_ade import LandingAIADE

        return LandingAIADE().v2.parse(document=path).markdown or ""
    except ImportError:
        pass
    try:  # legacy SDK, still widely installed
        from agentic_doc.parse import parse_documents  # type: ignore[import-not-found]

        results = parse_documents([str(path)])
        return results[0].markdown if results else ""
    except ImportError as exc:
        raise ExtractorUnavailable(
            f"extractor 'landingai' needs the SDK. Install it: {EXTRACTORS['landingai'].install}"
        ) from exc


_IMPLS: dict[str, Callable[[Path], str]] = {
    "pypdf": _pypdf,
    "pymupdf4llm": _pymupdf4llm,
    "docling": _docling,
    "llamaparse": _llamaparse,
    "landingai": _landingai,
}


# ---------------------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------------------


def available(name: str) -> bool:
    """True if this extractor could run right now (module importable, key present)."""
    import importlib.util

    extractor = EXTRACTORS.get(name)
    if extractor is None:
        return False
    if extractor.env and not os.environ.get(extractor.env):
        return False
    modules = {
        "pypdf": ["pypdf"],
        "pymupdf4llm": ["pymupdf4llm"],
        "docling": ["docling"],
        "llamaparse": ["llama_cloud_services", "llama_parse"],
        "landingai": ["landingai_ade", "agentic_doc"],
    }[name]
    return any(importlib.util.find_spec(m) is not None for m in modules)


def resolve(name: str) -> str:
    """Turn 'auto' into a concrete extractor name; validate an explicit one."""
    if name == "auto":
        for candidate in LOCAL_PREFERENCE:
            if available(candidate):
                return candidate
        return "pypdf"
    if name not in EXTRACTORS:
        raise SystemExit(f"unknown extractor '{name}'. Choose from: {', '.join(EXTRACTORS)}, auto")
    return name


def extract(path: Path, extractor: str = "auto") -> tuple[str, str]:
    """Extract markdown from one PDF. Returns (markdown, extractor_actually_used)."""
    resolved = resolve(extractor)
    return _IMPLS[resolved](path), resolved
