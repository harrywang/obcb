"""Download and split the open case corpus.

The case PDFs are third-party copyright and too large to ship, so the repo carries the
*recipe* instead: `data/case_list.json` records where each manuscript lives, its
sha256, and the page boundary between the student-facing case and the instructor
teaching note. This module turns that recipe back into a runnable corpus.

The list is ordered newest volume first, so fetching the first N means fetching the N
most recent cases. Use `obcb update-case-list` to refresh it as new volumes appear.

Each AABRI manuscript is one PDF holding the case narrative followed by a teaching note
with worked reference answers. Splitting it at the boundary produces the
``NAME.pdf`` / ``NAME_instructor.pdf`` pair the pipeline expects.

Boundaries were verified by hand, not detected at download time. Auto-detection would be
fragile in exactly the place it matters: several manuscripts put the last paragraph of
the narrative on the same page as the teaching-note heading, and a naive split truncates
the case. Those pages are deliberately included in both halves.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from rich.console import Console

from . import config

console = Console()

CASE_LIST_PATH = config.DATA_DIR / "case_list.json"
USER_AGENT = "obcb/0.1 (+https://github.com/open-business-case-bench)"
TIMEOUT = 60


def load_case_list(path: Path | None = None) -> dict:
    path = path or CASE_LIST_PATH
    if not path.exists():
        raise SystemExit(f"Case list not found at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def download(url: str, timeout: int = TIMEOUT) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read()


def write_slice(reader: PdfReader, first: int, last: int, out_path: Path) -> None:
    """Write pages [first, last] (1-indexed, inclusive) to a new PDF."""
    writer = PdfWriter()
    for page_no in range(first, last + 1):
        writer.add_page(reader.pages[page_no - 1])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as handle:
        writer.write(handle)


def run(
    limit: int | None = 1,
    out_dir: Path | None = None,
    case_list_path: Path | None = None,
    force: bool = False,
    timeout: int = TIMEOUT,
) -> list[str]:
    out_dir = out_dir or config.PDF_DIR
    case_list = load_case_list(case_list_path)
    available = case_list["cases"]
    cases = available if limit is None else available[:limit]

    console.print(
        f"Fetching {len(cases)} of {len(available)} case(s) in the case list "
        f"(newest volume first), from "
        f"[bold]{case_list['source'].split(' (')[0]}[/]\n-> {out_dir}\n"
    )

    fetched: list[str] = []
    for entry in cases:
        slug = entry["slug"]
        case_pdf = out_dir / f"{slug}.pdf"
        note_pdf = out_dir / f"{slug}_instructor.pdf"

        if case_pdf.exists() and note_pdf.exists() and not force:
            console.print(f"[dim]{slug}: already present, skipping[/]")
            fetched.append(slug)
            continue

        try:
            raw = download(entry["url"], timeout=timeout)
        except (urllib.error.URLError, TimeoutError) as exc:
            console.print(f"[red]{slug}: download failed ({exc})[/]")
            continue

        digest = hashlib.sha256(raw).hexdigest()
        if digest != entry["sha256"]:
            # The recorded page boundaries belong to a specific revision of the PDF.
            # A different file means the split would land in the wrong place.
            console.print(
                f"[yellow]{slug}: source PDF has changed since the case list was built[/]\n"
                f"  expected sha256 {entry['sha256'][:16]}..., got {digest[:16]}...\n"
                "  skipping - the recorded page split may no longer be correct."
            )
            continue

        reader = PdfReader(BytesIO(raw))
        if len(reader.pages) != entry["total_pages"]:
            console.print(
                f"[yellow]{slug}: expected {entry['total_pages']} pages, "
                f"got {len(reader.pages)}; skipping[/]"
            )
            continue

        write_slice(reader, *entry["case_pages"], case_pdf)
        write_slice(reader, *entry["instructor_pages"], note_pdf)

        c_first, c_last = entry["case_pages"]
        i_first, i_last = entry["instructor_pages"]
        overlap = " [dim](boundary page in both)[/]" if i_first <= c_last else ""
        console.print(
            f"[green]{slug}[/]: case pp.{c_first}-{c_last}, "
            f"instructor pp.{i_first}-{i_last}{overlap}"
        )
        fetched.append(slug)

    console.print(f"\n{len(fetched)}/{len(cases)} case pair(s) ready in [bold]{out_dir}[/]")
    if fetched:
        console.print("Next: [bold]uv run obcb extract[/]")
    return fetched
