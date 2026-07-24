"""Validate the case list and the splitting logic without touching the network.

The case list is what makes a fresh clone runnable, so a malformed entry is worse than a
failed download: it would silently produce mis-split pairs whose instructor half leaks
into the case half, handing the solver its own answer key.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from obcb import fetch

ok = True


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok
    ok = ok and cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")


case_list = fetch.load_case_list()
cases = case_list["cases"]

check("case list loads with cases", bool(cases), f"{len(cases)} cases")
check("case list documents source and licence", {"source", "license_note", "split_rule"} <= set(case_list))

slugs = [c["slug"] for c in cases]
check("slugs are unique", len(slugs) == len(set(slugs)))
check("ids are unique", len({c["id"] for c in cases}) == len(cases))
check(
    "slugs are filename-safe",
    all(re.fullmatch(r"[a-z0-9][a-z0-9-]*", s) for s in slugs),
)
check(
    "no slug ends in _instructor (would collide with the pair partner)",
    not any(s.endswith("_instructor") for s in slugs),
)

required = {"id", "slug", "title", "url", "total_pages", "case_pages", "instructor_pages", "sha256"}
check("every entry has the required fields", all(required <= set(c) for c in cases))
check("sha256 values look like digests", all(re.fullmatch(r"[0-9a-f]{64}", c["sha256"]) for c in cases))
check("urls are https", all(c["url"].startswith("https://") for c in cases))

bad_ranges = [
    c["slug"]
    for c in cases
    if not (
        c["case_pages"][0] == 1
        and c["case_pages"][0] <= c["case_pages"][1] < c["total_pages"]
        and c["instructor_pages"][1] == c["total_pages"]
        and c["instructor_pages"][0] <= c["instructor_pages"][1]
    )
]
check("page ranges are in bounds and case-before-instructor", not bad_ranges, str(bad_ranges))

# An overlap of one page is intentional (narrative tail + heading share a page).
# More than one would mean instructor content bleeding into the case half.
overlaps = {
    c["slug"]: c["case_pages"][1] - c["instructor_pages"][0] + 1
    for c in cases
    if c["instructor_pages"][0] <= c["case_pages"][1]
}
check(
    "overlaps never exceed one page",
    all(v <= 1 for v in overlaps.values()),
    f"overlapping: {overlaps}" if overlaps else "none",
)
check(
    "every case half has at least 3 pages of narrative",
    all(c["case_pages"][1] >= 3 for c in cases),
)

# The list is ordered newest first, because `fetch-cases --limit N` takes the first N
# and that is what makes the default set "the N most recent".
volumes = [c.get("volume") or 0 for c in cases]
check(
    "case list is sorted newest volume first",
    volumes == sorted(volumes, reverse=True),
    f"volumes {volumes[:6]}...",
)
check(
    "every entry records how its split was determined",
    all(c.get("split_source") in {"assistant-checked", "auto"} for c in cases),
    str(sorted({c.get("split_source") for c in cases})),
)
manual = [c for c in cases if c.get("split_source") == "assistant-checked"]
check("assistant-checked entries survive a rescan", len(manual) >= 1, f"{len(manual)} manual")

# --- splitting is correct on a synthetic PDF ------------------------------------------

tmp = Path(tempfile.mkdtemp(prefix="obcb-fetch-"))
src = tmp / "src.pdf"
writer = PdfWriter()
for _ in range(10):
    writer.add_blank_page(width=200, height=200)
with src.open("wb") as fh:
    writer.write(fh)

reader = PdfReader(str(src))
fetch.write_slice(reader, 1, 4, tmp / "a.pdf")
fetch.write_slice(reader, 4, 10, tmp / "b.pdf")
check("write_slice is inclusive on both ends", len(PdfReader(str(tmp / "a.pdf")).pages) == 4)
check("write_slice handles an overlapping range", len(PdfReader(str(tmp / "b.pdf")).pages) == 7)

# --- the shipped corpus, if present, matches the case list -----------------------------

from obcb import config  # noqa: E402

present = [c for c in cases if (config.PDF_DIR / f"{c['slug']}.pdf").exists()]
if present:
    mismatched = []
    for c in present:
        case_n = len(PdfReader(str(config.PDF_DIR / f"{c['slug']}.pdf")).pages)
        note_n = len(PdfReader(str(config.PDF_DIR / f"{c['slug']}_instructor.pdf")).pages)
        want_case = c["case_pages"][1] - c["case_pages"][0] + 1
        want_note = c["instructor_pages"][1] - c["instructor_pages"][0] + 1
        if (case_n, note_n) != (want_case, want_note):
            mismatched.append(c["slug"])
    check(
        "on-disk pairs match the case list page counts",
        not mismatched,
        f"{len(present)} present" + (f", bad: {mismatched}" if mismatched else ""),
    )
else:
    print("SKIP  on-disk pair check (run `obcb fetch-cases` first)")

print()
sys.exit(0 if ok else 1)
