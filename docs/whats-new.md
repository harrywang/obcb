# What this project adds to the original release

[← README](../README.md)

The paper's authors released a reference implementation (`reference-paper-code/`, git-ignored
here). It contains the **evaluation harness** — the code that runs solver models over an
already-built benchmark and grades them — plus the olmOCR service that converts case PDFs to
text. It does **not** contain the benchmark-construction code, and it cannot be run without the
licensed case corpus, which is not redistributable.

Open Business Case Bench keeps what the reference got right, reimplements what tied it to Docker
and a GPU, and adds everything needed to build an *open* benchmark from scratch and report on it.

## The original release, file by file

```
reference-paper-code/
  pipeline/
    evaluate_models.py                     # eval harness, orchestrated by DataDreamer
    prompts/evaluate_models_grading.py     # the solve + grade prompts
    utils/
      openrouter.py, llm_utils.py          # OpenRouter client wrappers
      data_utils.py                        # PDF pairing + JSONL helpers
      onet_utils.py, work_activities.json  # O*NET taxonomy
      limit.py
    Dockerfile, run_pipeline.sh            # containerised runner
    outputs/…/example_final_dataset.jsonl  # two schema-illustration records
  modal_ocr/                               # olmOCR 7B on Modal/Docker (PDF -> markdown)
  paper.pdf
```

There is **no** benchmark-construction stage in the release: no code that reads a case + teaching
note and produces questions, reference solutions, or rubrics. The paper describes that stage in
its Methods section; the code was not published.

## Three categories

### 1. Kept verbatim (for comparability with the paper)

| From the reference | Here |
|---|---|
| Solve prompt | `prompts/solve.md` — byte-identical, asserted against the reference source in `tests/prompts_check.py` |
| Grade prompt | `prompts/grade.md` — byte-identical (a lost trailing space was caught and restored; see [Prompts](prompts.md)) |
| Judge score parsing / clamping | `evaluate.py` — same `<<score>>` extraction and `[0, len(rubric)]` clamp |
| Filename pairing rule | `extract.py:pair_name` — same `NAME` / `NAME_instructor` convention as `data_utils.get_markdown_pair_name` |
| O*NET taxonomy | `data/onet/work_activities.json` — copied unchanged (sha256-verified identical) |

### 2. Reimplemented (same behaviour, no Docker / GPU / heavy deps)

| Concern | Reference | Here |
|---|---|---|
| Runtime | Two Docker images + `docker run` wrappers | plain Python; `uv run` installs deps on first use |
| Orchestration & resume | DataDreamer step graph (`datadreamer.dev==0.46.0`, pulling torch/transformers/datasets) | `llm.py`: ~200 lines of async + a JSONL response cache. Finer-grained — resumes at the individual call, not the whole step. All LLMs on one cache file share a single loaded copy (`get_cache`), opened once for appends. A failure is never memoised — including an **empty body returned as a normal 200**, which once silently poisoned cases on every re-run (see [Reliability](reliability.md)) |
| OpenRouter client | `utils/openrouter.py` subclassing DataDreamer's `OpenAI` | `llm.py`: a thin async `AsyncOpenAI` wrapper whose tenacity retries fire **only on transient failures** (timeouts, connection drops, 5xx, rate limits) and fail fast on 4xx, so a bad request never burns the retry budget; a blank response is treated as a transient failure and retried, not accepted |
| PDF → markdown | olmOCR 7B, GPU-only, one path | `extractors.py`: five pluggable extractors (`pymupdf4llm`, `pypdf`, `docling`, `llamaparse`, `landingai`) with an `auto` order — see [Extraction](extraction.md) |
| JSONL I/O | `utils/data_utils.py` | `jsonl.py`, including the reference's indented "pretty JSONL" dialect |

### 3. New — not in the release at all

| Area | What it is | Modules |
|---|---|---|
| **Benchmark construction** | The missing stage: profile a case, extract question/reference-solution pairs *from the instructor half only*, synthesise a checklist rubric, tag discipline / question type / O*NET. Implemented from the paper's Methods section. | `build.py`, `prompts/{case_profile,extract_questions,rubric,metadata,dwa}.md` |
| **An open corpus** | A curated list of 54 open-access cases (yielding **293 questions**), plus commands to fetch/split them and to scan the source journal for more — so the pipeline runs with no licensed material. | `fetch.py`, `discover.py`, `data/case_list.json` — see [Corpus](corpus.md) |
| **Incremental processing** | Every stage skips a case already in its output and merges new work in, so `obcb -3` after `obcb` processes only the two new cases and a re-run does nothing — distinct from the cache (which only makes re-billing free). `--force` reprocesses. This is what makes the corpus buildable in stages. | `fetch.py`, `extract.py`, `build.py`, `evaluate.py` |
| **Cost & time tracking** | Exact USD accounting from OpenRouter's inline `usage.cost`, split three ways per case — **construction** (extract + build, once per case), **solver** (each model's answers), and **judge** (grading, booked to the `(case, model)` pair it graded). Classified by *stage*, so the judge sharing the annotator's model id never blurs grade into construction. Each call's model time is recorded too, rolled up per case and shown beside cost, plus wall-clock per run. Printed, logged, and shown in the report; `obcb cost` for history. Two spend cuts fall out: a failed solve scores 0 directly and is never sent to the judge (no wasted grading call), and `OBCB_RUBRIC_MODEL` can point rubric writing at a cheaper model than the builder. | `usage.py` |
| **Reproducibility** | Every result-affecting setting in one env-overridable module, written to `run_config.json` and embedded in `scores.json`; prompts hashed in. Rubric generation is its own role (`OBCB_RUBRIC_MODEL`, defaulting to the builder) so it can be swapped and recorded independently. | `config.py` |
| **Richer metrics** | Standard **and** Complete Answer scoring, bootstrap 95% CIs, breakdowns by discipline / case / question type. The reference reports a single mean normalised score. | `report.py` |
| **HTML report** | A shareable single-file results page (editorial style, light by default), with a case & question detail view — each extracted question, its reference solution, rubric, and every model's answer and score — plus by-category, by-model (solve vs. judge), and by-case cost tables, and a **By case** section with score-per-case and cost-per-case line charts (cost split into total / construction / solver / judge). | `html_report.py` — see [HTML report](report.md) |
| **A CLI** | Bare `obcb` (or `-3` / `-all`) fetches and runs the whole pipeline over the first N cases; the rest are subcommands (`fetch-cases`, `config`, `extractors`, `prompts`, `cost`, `update-case-list`, …). | `cli.py` |
| **An offline test suite** | 194 assertions across 10 files, runnable with no API key, covering the plumbing, the three-way (construction / solver / judge) cost split and per-case timing, incremental processing and `--force`, the prompt loader, the case list and split detector, the HTML report, cross-process caching, the never-cache-a-failure guard (empty bodies included), the transient-vs-permanent retry policy, and the skip-grading-on-failed-solve path. | `tests/` — see [Development](development.md) |

## Why the split matters

The two things the reference could not give a newcomer were **a runnable corpus** (its cases are
licensed) and **the construction code** (unreleased). Those are exactly the two gaps OBCB fills —
which is why "reimplemented the harness" is the smaller half of the work and "built an open
benchmark around it" is the larger.
