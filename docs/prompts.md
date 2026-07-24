# Prompts and provenance

[← README](../README.md)

## Prompts

Prompts live as markdown in `src/obcb/prompts/`, one file each, so they can be read and
edited without opening Python. That is deliberate: whether `extract_questions.md` faithfully
recovers an instructor's question/answer pairs, and whether `rubric.md` turns a reference
solution into fair criteria, are pedagogy judgments rather than programming ones.

```bash
uv run obcb prompts                     # list them with digests and placeholders
uv run obcb prompts --show rubric       # print one in full
```

Each file carries frontmatter declaring its `description` and `placeholders`. The loader
validates that declaration against the body **at import time**, in both directions, so a
typo'd `{case_txt}` fails immediately with a clear message instead of raising `KeyError`
deep inside a stage after earlier calls were already paid for.

Every prompt is hashed into `run_config.json` and stamped into `report.md`, for the same
reason token budgets are: two runs with identical config can still differ because someone
edited a prompt.

`solve.md` and `grade.md` are **copied verbatim from the reference implementation** — that
is what keeps results comparable to the paper. Their baseline digests are recorded, and
`obcb prompts` warns when either has been modified.

## What is copied from the reference, and what is not

| Artifact | Provenance |
|---|---|
| `prompts/solve.md`, `prompts/grade.md` | **Byte-identical** to `reference-paper-code/pipeline/prompts/evaluate_models_grading.py`, asserted against that source file in `tests/prompts_check.py` |
| Judge score parsing and clamping in `evaluate.py` | Follows the reference exactly |
| Filename pairing (`NAME` / `NAME_instructor`) | Same rule as the reference's `get_markdown_pair_name` |
| `data/onet/work_activities.json` | Copied unchanged from the reference tree |
| Everything else | Written for this project — the pipeline, the response cache, cost accounting, the PDF extractors, the case list and scanner, the HTML report, and all five construction prompts |

The reference release contains the evaluation harness but not the benchmark-construction code,
so `build.py` and its prompts are ours, implemented from the paper's Methods section.

The byte-identity of the two copied prompts is checked against the reference **source file**, not
against a snapshot of this package. That distinction is not pedantic: an earlier copy of
`grade.md` had silently lost a trailing space, and a self-comparison confirmed it as correct for
several revisions before a check against the original caught it.
