"""Stage 1: paired PDFs -> cases_raw.jsonl.

The extractor is pluggable (see extractors.py). Local extractors are the default
so a clean checkout works offline and unmetered; hosted agentic parsers are opt-in for
scanned or otherwise hard documents.

Pairing follows the reference ``utils/data_utils.py``: ``NAME.pdf`` is the case,
``NAME_instructor.pdf`` is the instructor solution, unmatched files are dropped.
"""

from __future__ import annotations

import re
from pathlib import Path

from rich.console import Console

from . import config, extractors
from .jsonl import read_jsonl, write_jsonl

console = Console()

# A document whose markdown is thinner than this per page is almost certainly a scan
# that the chosen extractor cannot read, and needs an OCR-capable extractor instead.
MIN_CHARS_PER_PAGE = 200


def pair_name(path: Path) -> str:
    """Reference-compatible pair key (see data_utils.get_markdown_pair_name)."""
    name = path.name
    if name.endswith("_instructor.pdf"):
        return name[: -len("_instructor.pdf")]
    if name.endswith(".pdf"):
        return name[: -len(".pdf")]
    return path.stem


def normalize(text: str) -> str:
    """Collapse extraction artefacts without changing wording or table structure."""
    text = text.replace("­", "").replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def page_count(path: Path) -> int:
    from pypdf import PdfReader

    try:
        return len(PdfReader(str(path)).pages)
    except Exception:  # noqa: BLE001 - page count is only used for diagnostics
        return 0


def read_pdf(path: Path, extractor: str) -> tuple[str, dict]:
    markdown, used = extractors.extract(path, extractor)
    markdown = normalize(markdown)
    pages = page_count(path)
    return markdown, {
        "Source-File": path.name,
        "extractor": used,
        "pdf-total-pages": str(pages),
        "chars-per-page": str(round(len(markdown) / pages) if pages else 0),
        "has-markdown-tables": str(markdown.count("\n|") > 2),
    }


def discover(pdf_dir: Path) -> list[tuple[str, Path, Path]]:
    cases: dict[str, Path] = {}
    notes: dict[str, Path] = {}
    for path in sorted(pdf_dir.glob("*.pdf")):
        (notes if path.name.endswith("_instructor.pdf") else cases)[pair_name(path)] = path

    shared = sorted(set(cases) & set(notes))
    for orphan in sorted(set(cases) ^ set(notes)):
        console.print(f"[yellow]dropping unpaired:[/] {orphan}")
    return [(name, cases[name], notes[name]) for name in shared]


def run(
    pdf_dir: Path | None = None,
    out_path: Path | None = None,
    extractor: str | None = None,
    only: set[str] | None = None,
    force: bool = False,
) -> list[dict]:
    pdf_dir = pdf_dir or config.PDF_DIR
    out_path = out_path or config.CASES_RAW
    extractor = extractor or config.EXTRACTOR

    pairs = discover(pdf_dir)
    if only is not None:
        pairs = [p for p in pairs if p[0] in only]
    if not pairs:
        raise SystemExit(f"No case/_instructor PDF pairs found in {pdf_dir}")

    # Skip pairs already in cases_raw.jsonl (unless --force). Incremental by design:
    # re-extracting an unchanged PDF produces the same markdown, so it is pure waste.
    existing = {r["case_name"]: r for r in read_jsonl(out_path)} if out_path.exists() else {}
    todo = pairs if force else [p for p in pairs if p[0] not in existing]
    for name in (p[0] for p in pairs if p[0] in existing and not force):
        console.print(f"[dim]{name}: already extracted, skipping[/]")

    if not todo:
        console.print("All requested cases already extracted; nothing to do.")
        return [existing[p[0]] for p in pairs if p[0] in existing]

    # Only resolve/validate an extractor when there is actually a document to read.
    resolved = extractors.resolve(extractor)
    spec = extractors.EXTRACTORS[resolved]
    if not extractors.available(resolved):
        import os

        if spec.env and not os.environ.get(spec.env):
            raise SystemExit(
                f"extractor '{resolved}' needs {spec.env}. Add it to .env (see .env.example)."
            )
        raise SystemExit(f"extractor '{resolved}' is not installed. {spec.install}")
    console.print(
        f"Extractor: [bold]{resolved}[/] ({spec.kind}"
        f"{', markdown tables' if spec.tables else ', text only'}"
        f"{', OCR' if spec.ocr else ''})\n"
    )

    for name, case_pdf, note_pdf in todo:
        case_text, case_meta = read_pdf(case_pdf, resolved)
        note_text, note_meta = read_pdf(note_pdf, resolved)

        for label, meta in (("case", case_meta), ("instructor", note_meta)):
            if int(meta["chars-per-page"]) < MIN_CHARS_PER_PAGE:
                console.print(
                    f"[yellow]{name} ({label}): only {meta['chars-per-page']} chars/page[/] "
                    "- likely a scan. Try --extractor llamaparse or landingai."
                )

        existing[name] = {
            "case_name": name,
            "case_file": str(case_pdf),
            "case_text": case_text,
            "case_clean_text": case_text,
            "case_metadata": case_meta,
            "case_failed_extraction": len(case_text) < 500,
            "instructor_name": name,
            "instructor_file": str(note_pdf),
            "instructor_text": note_text,
            "instructor_clean_text": note_text,
            "instructor_metadata": note_meta,
            "instructor_failed_extraction": len(note_text) < 500,
        }
        tables = case_meta["has-markdown-tables"] == "True" or (
            note_meta["has-markdown-tables"] == "True"
        )
        console.print(
            f"[green]{name}[/]: case {len(case_text):,} chars "
            f"({case_meta['pdf-total-pages']}p), instructor {len(note_text):,} chars "
            f"({note_meta['pdf-total-pages']}p)" + (" [dim]tables[/]" if tables else "")
        )

    merged = list(existing.values())
    write_jsonl(out_path, merged)
    console.print(
        f"\nWrote {len(todo)} new case(s); {len(merged)} total -> [bold]{out_path}[/]"
    )
    return merged
