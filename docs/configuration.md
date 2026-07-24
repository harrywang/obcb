# Configuration, files, and cost

[← README](../README.md)

## Files the pipeline reads and writes

Everything the pipeline touches, and whether it is committed to git:

| File | Tracked? | What it is |
|---|---|---|
| `data/case_list.json` | yes | **The case list** — all 54 usable cases, newest first: where to download each and where to split it. Input to `obcb fetch-cases`. |
| `data/onet/work_activities.json` | yes | O*NET work-activity taxonomy. Required by `obcb build`. |
| `data/jbca_pairs/*.pdf` | no | The fetched corpus. Rebuild with `obcb fetch-cases`. |
| `data/outputs/cases_raw.jsonl` | no | Extracted case + instructor markdown, one row per case. |
| `data/outputs/cases.jsonl` | no | Cases with an LLM-written title, summary, and learning objectives. |
| `data/outputs/benchmark.jsonl` | no | The benchmark: one row per question, with reference solution, rubric, and tags. |
| `data/outputs/results/<model>.jsonl` | no | Each model's answers and per-question scores. |
| `data/outputs/report.md` | no | Human-readable scores, with breakdowns and lifetime spend. |
| `data/outputs/report.html` | no | The same results as a shareable single-file web page. |
| `data/outputs/scores.json` | no | The same metrics as data, with the run config embedded. |
| `data/outputs/run_config.json` | no | **The run config.** Every result-affecting setting resolved for the last run, including prompt hashes. Written by every pipeline command. |
| `data/outputs/usage.jsonl` | no | One record per run: tokens and USD spent. |
| `data/outputs/.cache/llm_cache.jsonl` | no | Memoised LLM responses, so a rerun resumes instead of re-billing. |

Every name here says what the file is: the **case list** lists cases, the **run config** records
the settings a run used.

## Configuration and reproducibility

All settings live in `src/obcb/config.py`, each overridable by an `OBCB_*` environment
variable and, where it makes sense, a CLI flag. Nothing that affects a result is hardcoded at
a call site. `.env.example` carries only the API key; every other setting is discoverable
through `obcb config` and documented inline in `config.py`.

```bash
uv run obcb config            # human-readable, grouped by section
uv run obcb config --json     # the same data, machine-readable
```

Because this is a benchmark, the settings that change the numbers are treated as part of the
result rather than as incidental configuration:

- **Sampling and token budgets** — a truncated answer scores lower, so a run is only comparable
  to another with the same budgets.
- **Construction quality gates** — these decide which questions exist at all. Different gates
  over identical source PDFs produce a different benchmark.
- **Bootstrap resamples and seed** — these determine the reported confidence intervals.

Every pipeline command writes the fully resolved configuration to
`data/outputs/run_config.json`, `scores.json` embeds it, and `report.md` stamps the key values
in its header. A set of numbers can always be traced back to what produced it.

Deliberately left at their call sites: the retry/backoff policy in `llm.py` and the
page-thinness warning threshold in `extract.py`. Neither changes a score, and hoisting them
would add indirection without payoff.

### Model roles

Five roles, each an `OBCB_*` override. The defaults come from the paper.

| Role | Env var | Default | What it does |
|---|---|---|---|
| builder | `OBCB_BUILDER_MODEL` | `google/gemini-2.5-pro` | Profiles cases and extracts question/solution pairs |
| annotator | `OBCB_ANNOTATOR_MODEL` | `google/gemini-2.5-flash` | Discipline, question-type flags, O*NET tags |
| judge | `OBCB_JUDGE_MODEL` | `google/gemini-2.5-flash` | Grades every answer against its rubric |
| solvers | `OBCB_SOLVERS` | `claude-sonnet-4.6, gpt-5.4, gemini-3-flash-preview` | The models being benchmarked |
| rubric | `OBCB_RUBRIC_MODEL` | (defaults to the builder) | Turns each reference solution into a checklist rubric — set to a cheaper model to cut per-question construction cost |

Change the **judge** only deliberately: it re-bills every previously graded answer (the cache
keys on model) and shifts absolute scores, so runs before and after are not comparable. The
paper found three different judges gave identical rankings, so a "better" judge buys little.
Solvers are the safe things to update — a new solver only bills itself.

## Cost tracking

Every command ends with a token and spend breakdown by model and stage, and appends a record to
`data/outputs/usage.jsonl`:

```
                      Usage and cost
 Model                  Stage        Calls  Prompt tok  Output tok  Cost USD
 google/gemini-2.5-pro  rubrics          2       2,468       1,134   $0.0082
 ...
 TOTAL                                  10      12,340       5,670   $0.0412
```

```bash
uv run obcb cost          # spend history across all runs, plus per-model totals
```

Cost comes from OpenRouter's `usage.cost` field, returned inline on every completion —
authoritative USD, no price list to maintain and no second API call. If you point
`OPENROUTER_BASE_URL` at an endpoint that does not report cost, those calls are counted and
flagged with `*` rather than silently recorded as free, so a total is never quietly wrong.

The cache stores the usage each response originally cost, so a cache hit reports what it *saved*
instead of showing a blank. `report` folds lifetime spend into `report.md` and `scores.json`.

Cost is split **three ways per case**:

- **Construction** — `extract` (free) + `build`, run once per case and shared across all models.
- **Solver** — each benchmarked model's own inference to answer the questions.
- **Judge** — the fixed judge grading those answers, booked to the `(case, model)` pair it graded
  (not to the judge's own model line) so each model's solve and grade sit side by side.

So a case's total is `construction + solver + judge`. The split is computed by **stage**, not by
model id — the two evaluation stages (`solving`, `grading`) are the solver and judge cost, and
everything else is construction. That distinction matters because the default judge and annotator
are the *same model* (`gemini-2.5-flash`); a model-id split would wrongly fold grading into
construction, a stage split does not. The report shows it as **by-category**, **by-model** (each
model's solve vs. judge), and **by-case** tables, and every LLM call is tagged with its case and
stage in the usage ledger (`by_case`, `by_case_model`, `by_case_model_stage`) so the numbers
reconcile exactly.

### The response cache

Every LLM response is cached **on disk** in `data/outputs/.cache/llm_cache.jsonl`, keyed by
model, prompt, and sampling settings. Because it is a file, the cache **persists across separate
runs**: run the pipeline today, run it again next week with the same models, and the second run
makes zero API calls. Only new work — a new model, a changed prompt, a new case — is billed; a
different judge or solver misses only for that model while the others still hit. Failures are
never cached, so a retry retries. Delete the file to force everything fresh.
