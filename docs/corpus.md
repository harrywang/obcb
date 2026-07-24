# The case corpus

[← README](../README.md) · [Licence and redistribution](licence.md)

The case PDFs are third-party copyright and too large to commit, so the repo carries the
**recipe rather than the bytes**. `data/case_list.json` records, for each case, its source
URL, sha256, and the verified page boundary between the student-facing narrative and the
instructor teaching note.

```bash
uv run obcb fetch-cases        # 1 case  — the most recent (the default)
uv run obcb fetch-cases -3     # 3 most recent
uv run obcb fetch-cases -5     # 5
uv run obcb fetch-cases -10    # 10
uv run obcb fetch-cases -all   # every case in the list
uv run obcb fetch-cases -n 7   # any other number
```

**The default is one case.** The list is ordered newest volume first, so that one is the most
recent case published. Running the whole corpus through the LLM stages costs real money, so the
default is the smallest thing that exercises the pipeline end to end — scale up deliberately.

The same counts drive the whole pipeline: bare `obcb` fetches and runs the first N cases end to
end.

```bash
uv run obcb          # one case, extract -> build -> evaluate -> report
uv run obcb -5       # five
uv run obcb -all     # the whole corpus
```

The case list holds **54 cases**. Each run prints `Fetching N of M case(s) in the case list`, so
the two numbers are always visible.

That 54 is what a full scan of the journal's 594 manuscripts yields — a 9% hit rate, because most
papers either have no teaching note or pose discussion questions without answering them. Rerun
`obcb update-case-list` as new volumes appear.

Each source manuscript is one free PDF from the
[AABRI Journal of Business Cases and Applications](https://www.aabri.com/jbca.html) containing
a case followed by a teaching note with worked reference answers. AABRI is fully open access —
readers may "read, download, and use the manuscripts and information therein for any lawful
purpose without permission" — though authors retain copyright, so *re-hosting* is a separate
question from *using* — see [Licence and redistribution](licence.md). `fetch-cases` splits it into
the `NAME.pdf` / `NAME_instructor.pdf` pair the pipeline expects, then verifies the sha256
before splitting — if a manuscript has been revised upstream, the recorded page boundary may no
longer be right, so it skips with a warning rather than producing a mis-split pair.

Boundaries are recorded in the case list rather than detected at download time, so a fetch is
reproducible. Where a single page holds both the narrative's last paragraph and the
teaching-note heading, that page is included in **both** halves — truncating the narrative is
worse than a page of duplication. **Unless the answers begin on that page too**, in which case
the case half stops before it: a clipped last paragraph is far better than handing the solver
its own answer key. 10 of the 54 have such a shared page.

## File naming — this is a hard requirement

One source PDF becomes **two files**, and the pipeline finds them by filename alone. There is no
index, no metadata sidecar: the names *are* the pairing.

```
data/jbca_pairs/
  jbca-green-funeral.pdf              <- the case      (pages 1-6 of the source PDF)
  jbca-green-funeral_instructor.pdf   <- the solution  (pages 7-22)
```

The rules:

| Rule | Why |
|---|---|
| The solution file is the case name plus the suffix `_instructor`, before `.pdf` | `extract` strips `_instructor.pdf` to get the pair key and matches on the remainder |
| Both halves must share an identical stem | `foo.pdf` + `Foo_instructor.pdf` will not pair — matching is exact and case-sensitive |
| A file with no partner is skipped | `extract` prints `dropping unpaired: NAME` rather than silently benchmarking a case with no reference answers |
| A case stem must not itself end in `_instructor` | `x_instructor.pdf` would be read as the solution half of a case named `x` |

Everything downstream keys off the stem: it becomes `case_name` in `cases_raw.jsonl`, then in
`benchmark.jsonl`, then the per-case breakdown in `report.md`. Pick something readable.

The convention comes from the reference implementation
(`reference-paper-code/pipeline/utils/data_utils.py:get_markdown_pair_name`), so corpora prepared
for either pipeline are interchangeable.

**Adding your own cases:** split your PDF however you like, name the two halves to match, and drop
them in `data/jbca_pairs/` — or point `OBCB_PDF_DIR` at your own directory. `fetch-cases` is only
a convenience for the AABRI corpus; `extract` neither knows nor cares where the PDFs came from.

The default ten span accounting, finance, IT, economics, ethics, strategy, leadership, and
operations.

## How the case list was built

`data/case_list.json` is the output of scanning **every manuscript the journal has published**.
The process is reproducible with one command, and the numbers below come from a full run.

```bash
uv run obcb update-case-list                      # rescan all 594 manuscripts (~20 min)
uv run obcb update-case-list --scan 40            # only the newest 40, for new volumes
uv run obcb update-case-list --cache ~/.obcb-pdfs # keep accepted PDFs, remember rejects
uv run obcb update-case-list --recheck            # re-examine previously rejected ones
```

A partial `--scan N` only re-derives the newest N; everything outside that window is carried
over untouched, so refreshing for a new volume never shrinks the list.

**On downloading:** a manuscript can only be judged after its pages are read, so every candidate
must be fetched once — there is no way to filter before downloading. What the scan avoids is
*keeping* them. With `--cache`, only accepted PDFs are stored; a reject is deleted and its id
recorded in `rejected.json` with the reason, so a later rescan skips it without downloading
again. A full scan therefore leaves ~54 PDFs on disk rather than 594, and the second run is
almost instant. Use `--recheck` to re-examine rejects after changing the detector.

**Step 1 — scrape the index.** `discover.scrape_index` reads
[the journal index](https://www.aabri.com/jbca.html) and returns every manuscript with its
title and volume. Volume headings (`<b>Volume 51 - May 2026:</b>`) partition the page and are
listed newest first, so document order is already recency order. That run found **594
manuscripts across 47 volumes** (6 through 52).

**Step 2 — download and judge each one.** Every manuscript is fetched and its page text
examined by `discover.analyze`, which applies three tests in order:

| Test | Rejects |
|---|---|
| Does a line *begin* with a teaching-note heading? (`TEACHING NOTES`, `INSTRUCTOR'S NOTES`, `SUGGESTED ANSWERS`) | Papers with no instructor half at all |
| Does the note contain worked answers? (`Suggested Answer`, `Sample Response`, `Instructor Key`, a line-anchored `Answer:`, …) | Notes that pose discussion questions but never answer them |
| Is there at least 3 pages of case before the heading? | Papers that are essentially all teaching note |

The heading test is anchored to the start of a line for a reason: several papers are *titled*
"… : Case and Teaching Notes" or mention the note in their abstract, and an unanchored match
fires on page 1 and swallows the entire case.

**Step 3 — locate the boundary.** The case half ends where the note begins. When the heading
page also carries the tail of the narrative, that page goes into **both** halves — duplicating a
page costs less than truncating the narrative.

That sharing is withheld in two situations, both of which would leak the answer key:

- the answers begin *after* the heading on that same page, or
- the heading **is** an answers heading (`Suggested Answers to Case Question 1`), so the answers
  begin at the heading itself.

**10 of the 54** accepted cases end up with a shared page.

**Step 4 — sort and merge.** Entries are sorted newest volume first, and any entry marked
`split_source: assistant-checked` is carried over untouched rather than re-derived.

### Results of the full scan

Of the 594 manuscripts, 592 downloaded successfully:

| Outcome | Count | Share |
|---|---|---|
| **Accepted by the detector** | **53** | **9.0%** |
| No teaching-note heading | 340 | 57.4% |
| Note poses questions but never answers them | 163 | 27.5% |
| Note starts too early — too little case | 36 | 6.1% |
| *(2 more failed to download)* | 2 | — |

The case list holds **54**: the 53 above, plus one case the detector rejects (see below) that is
retained because it was inspected and found good.

A 9% yield is the point, not a disappointment. The filter is deliberately biased toward
rejecting: missing a candidate costs nothing when there are 594, while admitting one whose
"reference solution" is really a list of discussion prompts would poison every rubric built
from it.

### How each boundary was derived

> #### No human has reviewed any case in this list
>
> Both labels below describe **machine-derived** boundaries, differing only in how much evidence
> the machine used. Nobody has read these documents end to end, and no one with business-teaching
> expertise has judged whether the reference solutions are sound. `case_list.json` records this
> as `human_reviewed: false`.

| `split_source` | Count | What was actually done |
|---|---|---|
| `auto` | 44 | Boundary from the detector rule alone. |
| `assistant-checked` | 10 | An AI assistant also inspected the extracted text around the heading and confirmed the note contains worked answers. Still machine-derived. **Never overwritten by a rescan.** |
| `human-verified` | 0 | Reserved for entries a person has actually opened and confirmed. |

Read `assistant-checked` as "the page boundary is probably right", not as a quality guarantee.

### Split review

Every one of the 54 splits was then checked mechanically: for answer content leaking into the
case half, for the instructor half actually containing worked answers, for the heading opening
the instructor half, and for implausibly thin case halves. Results are recorded under
`split_review` in `case_list.json`.

| Result | |
|---|---|
| Splits leaking answer content into the case half | **0** |
| Boundaries corrected during the review | **7** |
| Instructor halves with no machine-detectable answers | 1 (`jbca-green-funeral`, answers are unlabelled prose) |

The review found two wrong rules, both now fixed and covered by tests:

1. A page holding the narrative tail *and* the heading was given to the case half even when the
   answers began on that same page — putting the answer key in front of the solver.
2. A heading that **is** an answers heading (`Suggested Answers to Case Question 1`) was not
   recognised as the answers starting, because the check looked only at text *after* the match,
   and the heading had consumed the very words being searched for.

**Known limitation:** 18 case halves do not end on terminal punctuation. Sampling shows most end
in an exhibit table or bullet list, which is expected, but a few look genuinely cut mid-sentence.
Telling those apart needs a person.

The rescan exemption is load-bearing rather than ceremony: `jbca-green-funeral` answers its
questions in unlabelled prose with no marker of any kind, so the detector rejects it outright.
A blind rebuild would silently drop a case that inspection showed to be good.

To mark an entry `human-verified`: open the PDF at the boundary, confirm the case half ends
where it should and that the teaching note genuinely works through its answers, then set
`split_source` and add `disciplines` and `question_mix` notes. `update-case-list` preserves any
entry not marked `auto`, so a verified entry is never re-derived.

`obcb find-cases` runs the same scan **without writing anything**, printing candidates for
review — useful when you want to eyeball before changing the list.
