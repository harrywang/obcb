# Open Business Case Bench (`obcb`)

An open pipeline for benchmarking AI on **business case analysis**.

This project is based on *"Frontier AI performance across the business disciplines: a
case-grounded benchmark of knowledge work and analytical reasoning"*
([arXiv:2607.16057](https://arxiv.org/abs/2607.16057)), which developed BusinessCaseBench from
238 licensed business school cases. Because those cases cannot be redistributed, the benchmark
cannot be reproduced without a case-clearinghouse licence — the gap OBCB closes in two ways:

1. **An open corpus.** Scripts that download and split case / instructor-solution pairs from
   openly published sources, so the whole pipeline runs end to end with no licensed material.
2. **An open pipeline.** uv-managed Python, no Docker, no GPU, pluggable PDF extraction, and
   per-run token and cost accounting.

## Setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) (the Python package
manager), then set your API key:

```bash
cp .env.example .env      # then add your OPENROUTER_API_KEY
```

One OpenRouter key covers every model call — construction, solving, and judging; it is the only
thing `.env` needs. There is no separate install step: `uv run` installs the locked dependencies
on first use.

## Data

The benchmark is built from **54 business cases**, every usable one found by scanning all 594
manuscripts in the [AABRI Journal of Business Cases and Applications](https://www.aabri.com/jbca.html)
— the ones with a teaching note that actually works through its answers (a ~9% yield; most papers
have no note, or pose questions they never answer). The case PDFs are third-party copyright and
not committed; the repo ships `data/case_list.json` (source URL, sha256, and page split per case),
and the pipeline downloads and splits them on demand. See details at
[Corpus → How the case list was built](docs/corpus.md#how-the-case-list-was-built).

Together they yield **293 benchmark questions**. The builder labels each case
**fictional-vs-real** (invented/disguised organization vs. a real named company) while profiling
it: **43 fictional and 11 real** cases (240 vs. 53 questions). The report breaks scores out along
this split, since a real company invites pre-training recall in a way an invented one does not.
The label is an LLM judgement recorded per case, not a hand annotation.

## Run

`obcb` with no subcommand fetches and runs cases from the top of the list, straight through
extract -> build -> evaluate -> report:

```bash
uv run obcb            # the first case (the default)
uv run obcb -3         # the first 3
uv run obcb -all       # every case in the list
open data/outputs/report.html
```

Every stage is **incremental**: a case already fetched, extracted, built, and evaluated is
skipped, not re-run — so `obcb -3` after `obcb` only processes the two new cases, and re-running
the same command does nothing. Outputs accumulate (the report grows to cover every case processed
so far). Pass `--force` to reprocess from scratch. On top of that, every LLM response is cached in
a local, git-ignored file (`data/outputs/.cache/llm_cache.jsonl`), so even a `--force` re-run bills
only genuinely new work; delete it to re-bill everything. `obcb fetch-cases` is the download-only
step, with the same counts, if you want to pre-stage cases without running them.

**Tune two knobs for a full run:**

```bash
OBCB_CONCURRENCY=24 OBCB_REQUEST_TIMEOUT=240 uv run obcb -all
```

- **`OBCB_CONCURRENCY` (default 8)** is tuned for a single case; on the whole corpus it is the
  bottleneck, because the builder model spends ~80s on each question-extraction call. A 54-case
  run at the default projects to **6+ hours**; at 24 it finishes in about **70 minutes**.
- **`OBCB_REQUEST_TIMEOUT` (default 600s)** is how long one HTTP request waits before it is
  aborted and retried. 600s is generous headroom so a legitimately slow reasoning call is never
  killed — fine for a single case, but on a full run a provider occasionally *hangs* a request,
  and with up to 4 retries one hung call ties up its slot for `4 × timeout ≈ 40 min`. Lowering it
  to 240s (still ~3× a normal call) caps a hang at ~16 min so the run keeps moving. See the
  [reliability notes](docs/reliability.md) for the flakiness that motivates it.

Interrupting is safe: the response cache tolerates a torn final line, and everything already
completed replays for free on the next run. The one thing you lose is that run's cost record —
the ledger is written to `usage.jsonl` only when the command finishes, so spend from a killed
run is billed but never reported.

## Results

A reference run over the full corpus — **54 cases, 293 questions**, three frontier solvers graded
by a fixed judge. Standard is the rubric-weighted fraction of criteria met; Complete Answer is the
share of questions where every criterion is met (whiskers below are bootstrap 95% CIs).

| Model | Standard | Complete Answer |
|---|---|---|
| `anthropic/claude-sonnet-4.6` | **78.8%** [76.0, 81.7] | **42.0%** [36.2, 47.4] |
| `openai/gpt-5.4` | 75.0% [72.0, 78.2] | 33.8% [28.3, 39.2] |
| `google/gemini-3-flash-preview` | 70.9% [67.5, 73.8] | 30.7% [25.6, 35.8] |

The whole run cost **$39.42** (construction $8.52, solver $28.35, judge $2.55) and took about 70
minutes at `OBCB_CONCURRENCY=24`. The full interactive report — per-case and per-discipline
breakdowns, cost and timing charts, and every question with its rubric and each model's answer — is
published at **[harrywang.github.io/obcb](https://harrywang.github.io/obcb/)** (this reference run);
running the pipeline yourself writes the same page to `data/outputs/report.html`.

Treat these as a **pipeline check, not a capability measurement**: the cases are openly published,
so pre-training contamination is expected, and the labels come from LLMs (builder, annotator,
judge), not human annotation.

## Pipeline

Five steps, each a command backed by one module. Every step reads and writes files on disk, so
any step can be rerun on its own (`uv run obcb <step>`) and the intermediate state is inspectable.

| # | Command | Reads | Writes | What it does |
|---|---|---|---|---|
| 1 | `fetch-cases` | `case_list.json` | `jbca_pairs/*.pdf` | Downloads the newest N source PDFs (default 1), checks each sha256, splits at the recorded page boundary |
| 2 | `extract` | `jbca_pairs/*.pdf` | `cases_raw.jsonl` | Pairs the two halves by filename and converts both to markdown; warns on pages that look scanned |
| 3 | `build` | `cases_raw.jsonl` | `cases.jsonl`, `benchmark.jsonl` | Extracts question/reference-solution pairs **from the instructor half only**, writes a checklist rubric, tags discipline / question type / O*NET |
| 4 | `evaluate` | `benchmark.jsonl` | `results/<model>.jsonl` | Single-turn solve with the full case in context, then grades each answer against its rubric with a fixed judge |
| 5 | `report` | `results/*.jsonl` | `report.md`, `report.html`, `scores.json` | Standard and Complete Answer scores, bootstrap CIs, breakdowns, and lifetime spend |

Steps 1 and 2 are deterministic and free. Steps 3 and 4 call models and cost money; every
response is cached on disk, so a re-run with the same models makes zero API calls and only new
work is billed. See [Cost tracking](docs/configuration.md#cost-tracking). Bare `obcb` (or
`obcb -3`, `obcb -all`) runs all five steps in one go; the table is for running or re-running a
step on its own.

Quality gates in `build` mirror the paper: a question is dropped unless the instructor solution
states an explicit reference answer, and again unless a usable rubric (at least 2 criteria) comes
back. Both thresholds are configurable and recorded in the run config.

## Models

Four roles, each an `OBCB_*` override. The defaults follow the paper — smaller models for
construction, a fixed judge held constant across solvers so scores stay comparable, and three
frontier models as the solvers under test.

| Role | Default | Override | What it does |
|---|---|---|---|
| **builder** | `google/gemini-2.5-pro` | `OBCB_BUILDER_MODEL` | Profiles cases, extracts questions/solutions, writes rubrics |
| **annotator** | `google/gemini-2.5-flash` | `OBCB_ANNOTATOR_MODEL` | Discipline, question-type, and O*NET tags |
| **solvers** | `anthropic/claude-sonnet-4.6`, `openai/gpt-5.4`, `google/gemini-3-flash-preview` | `OBCB_SOLVERS` or `--model` | The models being benchmarked |
| **judge** | `google/gemini-2.5-flash` | `OBCB_JUDGE_MODEL` | Grades every answer against its rubric |

```bash
uv run obcb evaluate --model anthropic/claude-sonnet-4.6   # one run, solvers only
OBCB_SOLVERS=a,b,c OBCB_JUDGE_MODEL=... uv run obcb -all    # persistent, any role
```

Swap **solvers** freely — a new solver only bills itself, since the cache keys on model. Change
the **judge** deliberately: it re-bills every previously graded answer and shifts absolute scores,
so runs before and after are not comparable (the paper found three different judges gave identical
rankings, so a "better" judge buys little). Every choice is recorded in `run_config.json`. See
[Configuration → model roles](docs/configuration.md#model-roles).

## Cost

Every call's USD cost comes straight from OpenRouter's inline `usage.cost`, so accounting is
exact — no price list to maintain. Every dollar falls into one of **three categories**:

- **Construction** — `extract` + `build`, run **once per case**. Extract is free (deterministic);
  build is the case-profile, question-extraction, rubric, and annotation calls. Shared across all
  benchmarked models, charged to the case.
- **Solver** — each benchmarked model's own inference to answer the questions.
- **Judge** — the fixed judge grading those answers. Held separate from the solver so "how much
  this model costs to run" and "how much it cost to grade it" never blur together, even though the
  judge is booked to the `(case, model)` pair it graded.

So **case total = construction + solver + judge**. The report shows the split four ways — a run
total, a **by-category** table (construction / solver / judge), a **by-model** table (each model's
solve vs. judge), and a **by-case** table — and `obcb cost` prints spend history across runs.
Construction is cached, so it is a one-time cost per case; to trim it further, point
`OBCB_RUBRIC_MODEL` at a cheaper model than the builder. See
[Configuration → cost tracking](docs/configuration.md#cost-tracking).

## Documentation

| Doc | What it covers |
|---|---|
| [Corpus](docs/corpus.md) | Fetching cases, the filename-pairing rule, and how the 54-case list was built and reviewed |
| [Configuration](docs/configuration.md) | Every setting and file, model roles, reproducibility, the response cache, and cost tracking |
| [Extraction](docs/extraction.md) | The five PDF-to-markdown extractors and when to reach for each |
| [Prompts](docs/prompts.md) | The markdown prompt templates, and what is copied verbatim from the reference |
| [HTML report](docs/report.md) | The shareable single-file results page |
| [Development](docs/development.md) | Running the offline test suite and the source layout |
| [Reliability](docs/reliability.md) | Handling flaky providers — empty responses cached as valid, request hangs, and the fixes |
| [What's new](docs/whats-new.md) | What this project keeps verbatim, reimplements, and adds on top of the paper authors' release |
| [Licence](docs/licence.md) | AABRI's open-access terms and what they mean for redistributing results |

## Licence

The code is **MIT licensed** — see [LICENSE](LICENSE). The **case materials** are open access
but remain under their authors' copyright: you may fetch and use them, but re-hosting the PDFs
or publishing the extracted reference solutions needs author permission
([docs/licence.md](docs/licence.md)). Note that the default PDF extractor, `pymupdf4llm`, is
AGPL-3.0; `docling` (MIT) is a permissive alternative.
