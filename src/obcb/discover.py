"""Find new cases to add to the case list.

Growing the corpus means answering three questions about a candidate manuscript: does it
contain a teaching note, does that note actually work through answers (rather than just
listing discussion prompts), and where exactly does the case end and the note begin.

The first two are mechanical and this module automates them. The third is mechanical
*most* of the time, but not always: some manuscripts put the last paragraph of the
narrative on the same page as the teaching-note heading. Split after that page and the
solver never sees the end of the case; split before it and the answer key leaks into the
case half.

That page is therefore given to both halves - except when the answers also start on it,
either after the heading or *as* the heading ("Suggested Answers to Case Question 1"). A
clipped last paragraph is a much smaller problem than handing the solver its answer key,
so leaking is never traded for completeness.

The judgment call - is this a genuine reference solution? - stays with a person. This
prints candidates for review; it never edits the case list.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
import urllib.error
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from rich.console import Console

from .fetch import CASE_LIST_PATH, download, load_case_list

console = Console()

INDEX_URL = "https://www.aabri.com/jbca.html"

_LINK = re.compile(r'<a[^>]+href="(manuscripts/(\d+)\.pdf)"[^>]*>(.*?)</a>', re.S | re.I)
# Volume headings partition the index: "<b>Volume 51 - May 2026:</b>". Volumes are listed
# newest first, so document order is already recency order.
_VOLUME = re.compile(r"<b>\s*Volume\s+(\d+)\s*(?:-\s*([^:<]+?))?\s*:?\s*</b>", re.I)

# The heading that starts the instructor half. Anchored to the start of a line: several
# manuscripts are titled "... : Case and Teaching Notes" or mention the note in their
# abstract, and an unanchored match fires on page 1 and swallows the whole case.
_HEADING = re.compile(
    r"^[ \t]*(TEACHING\s+NOTES?|INSTRUCTOR[''’]?S?\s+(NOTES?|MANUAL)|"
    r"SUGGESTED\s+(ANSWERS?|SOLUTIONS?))\b",
    re.I | re.M,
)
# Evidence the note works through answers rather than only posing questions. Journals
# vary: some label a worked answer "Suggested Answer", others put "Answer:" on its own
# line, sometimes behind a bullet. Both phrase and label forms are accepted.
#
# This is deliberately biased toward false negatives. Missing a candidate costs nothing -
# there are hundreds more. Admitting one whose "reference solution" is really a list of
# discussion prompts would poison every rubric built from it. Some manuscripts answer
# their questions in unlabelled prose and no marker can catch them; those need a reader.
_ANSWERS = re.compile(
    r"(suggested\s+answer|sample\s+response|suggested\s+response|answers?\s+to\s+(the\s+)?"
    r"(discussion|case|assignment)|possible\s+answer|solution\s+to\s+question"
    r"|instructor\s+key"
    r"|^[ \t]*(?:[-*•●]\s*|\d+[.)]\s*)?(answer|response|solution)\s*:)",
    re.I | re.M,
)
# A heading that announces the answers themselves, rather than a notes section.
_ANSWERS_HEADING = re.compile(r"\s*SUGGESTED\s+(ANSWERS?|SOLUTIONS?)", re.I)

# Running header on every page; ignored when judging whether a page has real narrative.
_RUNNING_HEADER = re.compile(r"^\s*Journal of Business Cases.*?$", re.M | re.I)

# A heading page needs at least this much narrative before the heading to count as a
# shared boundary page that belongs in both halves.
MIN_OVERLAP_CHARS = 400
# Below this many pages of narrative there is not enough case to reason about.
MIN_CASE_PAGES = 3


def scrape_index(timeout: int = 60) -> list[dict]:
    """Fetch the journal index: every manuscript with its title and volume.

    Returned in document order, which the journal publishes newest volume first. That
    ordering is what makes "the default ten" mean "the ten most recent".
    """
    page = download(INDEX_URL, timeout=timeout).decode("utf-8", errors="ignore")

    # Walk the page once, tracking the most recent volume heading seen.
    marks = [(m.start(), m.group(1), (m.group(2) or "").strip()) for m in _VOLUME.finditer(page)]
    seen: dict[str, dict] = {}
    for match in _LINK.finditer(page):
        path, mid, label = match.groups()
        if mid in seen:
            continue
        volume, published = None, ""
        for pos, vol, date in marks:
            if pos < match.start():
                volume, published = int(vol), date
            else:
                break
        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", label))).strip()
        seen[mid] = {
            "id": mid,
            "url": f"https://www.aabri.com/{path}",
            "title": title,
            "volume": volume,
            "published": published,
        }
    return list(seen.values())


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return "jbca-" + "-".join(slug.split("-")[:4])


def analyze(pages: list[str]) -> dict:
    """Judge a manuscript from its page texts. Pure logic, so it is testable directly.

    Returns {'ok': bool, 'reason': str} plus the proposed split when ok.
    """
    total = len(pages)
    heading_page = next((i for i, text in enumerate(pages) if _HEADING.search(text)), None)
    if heading_page is None:
        return {"ok": False, "reason": "no teaching-note heading"}
    if heading_page < MIN_CASE_PAGES:
        return {"ok": False, "reason": f"note starts on page {heading_page + 1}, too little case"}

    note_text = "\n".join(pages[heading_page:])
    if not _ANSWERS.search(note_text):
        return {"ok": False, "reason": "note poses questions but never answers them"}

    # Does the heading page also carry the tail of the narrative? If so it belongs in both
    # halves - truncating the narrative is worse than duplicating a page.
    #
    # Unless the answers begin on that same page. Then handing it to the case half would put
    # the answer key in front of the solver, which is far worse than a clipped last paragraph.
    # Leaking beats truncation only while nothing is leaked.
    page_text = pages[heading_page]
    match = _HEADING.search(page_text)
    before = _RUNNING_HEADER.sub("", page_text[: match.start()]).strip()
    # Answers may begin on the heading page in two ways: after the heading, or *as* the
    # heading - "Suggested Answers to Case Question 1" is both. The second case matched
    # nothing when only the text after the match was searched, because the heading had
    # consumed the very words being looked for.
    heading_announces_answers = bool(_ANSWERS_HEADING.match(match.group(0)))
    answers_on_heading_page = heading_announces_answers or bool(
        _ANSWERS.search(page_text[match.end() :])
    )
    overlap = len(before) >= MIN_OVERLAP_CHARS and not answers_on_heading_page

    case_end = heading_page + 1 if overlap else heading_page
    instructor_start = heading_page + 1
    if case_end < MIN_CASE_PAGES or case_end >= total:
        return {"ok": False, "reason": "case half too short after splitting"}

    return {
        "ok": True,
        "reason": "",
        "total_pages": total,
        "case_pages": [1, case_end],
        "instructor_pages": [instructor_start, total],
        "overlap": overlap,
        "note_chars": len(note_text),
    }


def inspect(raw: bytes) -> dict:
    """Judge one manuscript PDF, adding its sha256 when accepted."""
    try:
        reader = PdfReader(BytesIO(raw))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - a corrupt PDF is just a rejected candidate
        return {"ok": False, "reason": f"unreadable PDF ({type(exc).__name__})"}
    verdict = analyze(pages)
    if verdict["ok"]:
        verdict["sha256"] = hashlib.sha256(raw).hexdigest()
    return verdict


REJECTED_LEDGER = "rejected.json"


def _load_rejected(cache_dir: Path | None) -> dict[str, str]:
    """Manuscript ids already judged unusable, with the reason."""
    if not cache_dir:
        return {}
    path = cache_dir / REJECTED_LEDGER
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_rejected(cache_dir: Path | None, rejected: dict[str, str]) -> None:
    if not cache_dir:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / REJECTED_LEDGER).write_text(
        json.dumps(rejected, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )


def build_case_list(
    scan: int | None = None,
    delay: float = 0.3,
    timeout: int = 60,
    cache_dir: Path | None = None,
    out_path: Path | None = None,
    verbose: bool = False,
    recheck: bool = False,
) -> dict:
    """Rescan the journal and rewrite the case list, newest volume first.

    Entries not marked ``split_source: auto`` are preserved verbatim. Their boundaries came
    from inspecting the surrounding text rather than the rule alone, which the rule cannot
    replicate: one of them answers its questions in unlabelled prose and is rejected outright.
    Everything else is redetected, so the list tracks the journal as new volumes appear.

    Note that no entry in the list has been reviewed by a person. See ``split_source_meaning``
    in the case list for exactly what each label does and does not claim.
    """
    out_path = out_path or CASE_LIST_PATH
    existing = load_case_list(out_path) if out_path.exists() else {"cases": []}
    kept = {c["id"]: c for c in existing.get("cases", []) if c.get("split_source") != "auto"}

    full_index = scrape_index(timeout=timeout)
    index = full_index if scan is None else full_index[:scan]
    # A partial scan must not delete what it did not look at. Entries outside the window
    # are carried over untouched; only the scanned range is re-derived.
    scanned_ids = {m["id"] for m in index}
    # Anything not in the window is carried over, checked or not. Checked entries that ARE
    # in the window are re-appended in the loop below, so nothing is duplicated or dropped.
    carried = [c for c in existing.get("cases", []) if c["id"] not in scanned_ids]

    # A manuscript can only be judged after its pages are read, so every candidate must be
    # downloaded once. What it need not do is keep them: only accepted PDFs land in the
    # cache, and rejects are recorded by id so a rescan skips them without downloading.
    rejected_before = {} if recheck else _load_rejected(cache_dir)
    console.print(
        f"Scanning {len(index)} manuscript(s), newest first. "
        f"{len(kept)} assistant-checked entr(ies) kept as-is"
        + (f", {len(carried)} outside the scan window carried over" if carried else "")
        + (f", {len(rejected_before)} previously rejected skipped" if rejected_before else "")
        + ".\n"
    )

    entries: list[dict] = list(carried)
    rejected: dict[str, str] = dict(rejected_before)
    skipped = 0
    for n, meta in enumerate(index, 1):
        if meta["id"] in kept:
            entries.append(kept[meta["id"]])
            continue
        if meta["id"] in rejected_before:
            skipped += 1
            continue

        raw = None
        cached = (cache_dir / f"{meta['id']}.pdf") if cache_dir else None
        if cached and cached.exists():
            raw = cached.read_bytes()
        else:
            try:
                raw = download(meta["url"], timeout=timeout)
            except (urllib.error.URLError, TimeoutError) as exc:
                if verbose:
                    console.print(f"[dim]{meta['id']}: download failed ({exc})[/]")
                continue
            time.sleep(delay)

        verdict = inspect(raw)
        if not verdict["ok"]:
            rejected[meta["id"]] = verdict["reason"]
            if cached and cached.exists():
                cached.unlink()  # judged unusable: do not keep the bytes
            if verbose:
                console.print(f"[dim]{meta['id']}: {verdict['reason']}[/]")
            continue

        if cached and not cached.exists():
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(raw)

        entries.append({
            "id": meta["id"],
            "slug": slugify(meta["title"]),
            "title": meta["title"],
            "volume": meta["volume"],
            "published": meta["published"],
            "url": meta["url"],
            "total_pages": verdict["total_pages"],
            "case_pages": verdict["case_pages"],
            "instructor_pages": verdict["instructor_pages"],
            "sha256": verdict["sha256"],
            "split_source": "auto",
        })
        if n % 25 == 0 or verbose:
            console.print(f"[dim]  {n}/{len(index)} scanned, {len(entries)} kept[/]")

    # Newest volume first, so "the default ten" means the ten most recent.
    entries.sort(key=lambda c: (-(c.get("volume") or 0), -int(c["id"])))

    # Slugs must stay unique: they become filenames and case_name downstream.
    seen: dict[str, int] = {}
    for entry in entries:
        base = entry["slug"]
        if base in seen:
            seen[base] += 1
            entry["slug"] = f"{base}-{seen[base]}"
        else:
            seen[base] = 1

    payload = {k: v for k, v in existing.items() if k != "cases"}
    payload["cases"] = entries
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    _save_rejected(cache_dir, rejected)

    n_checked = sum(1 for e in entries if e.get("split_source") != "auto")
    console.print(
        f"\n[bold]{len(entries)} case(s)[/] written to {out_path} "
        f"({n_checked} assistant-checked, {len(entries) - n_checked} auto-detected); "
        f"{len(rejected)} manuscript(s) rejected"
        + (f", {skipped} skipped from a previous scan" if skipped else "")
        + "."
    )
    if cache_dir:
        n_cached = len(list(cache_dir.glob("*.pdf")))
        console.print(
            f"Cache holds {n_cached} accepted PDF(s); rejects recorded in {REJECTED_LEDGER}."
        )
    return payload


def run(
    scan: int = 25,
    limit: int = 5,
    out_path: Path | None = None,
    delay: float = 0.5,
    timeout: int = 60,
    verbose: bool = False,
) -> list[dict]:
    """Scan candidate manuscripts and print those worth adding to the case list."""
    known = {c["id"] for c in load_case_list()["cases"]}

    console.print(f"Fetching the journal index from [bold]{INDEX_URL}[/] ...")
    index = [m for m in scrape_index(timeout=timeout) if m["id"] not in known]
    console.print(
        f"{len(index)} manuscript(s) not already in the case list; "
        f"inspecting up to {scan}, keeping up to {limit}.\n"
    )

    found: list[dict] = []
    for entry in index[:scan]:
        if len(found) >= limit:
            break
        try:
            raw = download(entry["url"], timeout=timeout)
        except (urllib.error.URLError, TimeoutError) as exc:
            if verbose:
                console.print(f"[dim]{entry['id']}: download failed ({exc})[/]")
            continue
        time.sleep(delay)  # be a considerate client of a small publisher's server

        verdict = inspect(raw)
        if not verdict["ok"]:
            if verbose:
                console.print(f"[dim]{entry['id']}: {verdict['reason']}[/]")
            continue

        candidate = {
            "id": entry["id"],
            "slug": slugify(entry["title"]),
            "title": entry["title"],
            "url": entry["url"],
            "total_pages": verdict["total_pages"],
            "case_pages": verdict["case_pages"],
            "instructor_pages": verdict["instructor_pages"],
            "sha256": verdict["sha256"],
        }
        found.append(candidate)
        mark = " [dim](boundary page in both halves - check this one)[/]" if verdict["overlap"] else ""
        console.print(
            f"[green]{candidate['slug']}[/] ({entry['id']}): {verdict['total_pages']}p, "
            f"case 1-{verdict['case_pages'][1]}, note {verdict['instructor_pages'][0]}-"
            f"{verdict['total_pages']}{mark}\n    {entry['title'][:88]}"
        )

    if not found:
        console.print("[yellow]No candidates passed. Try a larger --scan.[/]")
        return []

    blob = json.dumps(found, indent=2)
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(blob + "\n", encoding="utf-8")
        console.print(f"\nWrote {len(found)} candidate(s) -> [bold]{out_path}[/]")
    else:
        console.print(f"\n[bold]{len(found)} candidate entr(ies)[/], ready to review:\n")
        console.print(blob)

    console.print(
        "\n[yellow]Review before adding.[/] Open each teaching note and confirm the answers are "
        "real worked solutions, then append the entries to data/case_list.json with a "
        "`disciplines` and `question_mix` note."
    )
    return found
