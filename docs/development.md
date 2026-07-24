# Development

[← README](../README.md)

## Verifying without an API key

The whole pipeline is testable offline. Each check is a standalone script that prints
`PASS`/`FAIL` lines and exits non-zero on failure.

```bash
uv run obcb extract
uv run python tests/offline_check.py   # 15 plumbing assertions, LLM stubbed
uv run python tests/cost_check.py      # 27 accounting assertions, mock HTTP server
uv run python tests/config_check.py    # 14 assertions that env overrides reach call sites
uv run python tests/prompts_check.py   # 15 assertions on the prompt loader
uv run python tests/fetch_check.py     # 18 assertions on the case list and splitting
uv run python tests/discover_check.py  # 21 assertions on candidate detection
uv run python tests/html_check.py      # 32 assertions on the HTML report
uv run python tests/cache_check.py     # 17 assertions on caching and the retry policy
uv run python tests/eval_check.py      #  5 assertions on skipping failed-solve grading
uv run python tests/skip_check.py      # 16 assertions on incremental processing and --force
```

- `offline_check` — the plumbing: quality gates drop bad rows, invalid disciplines coerce to
  `Other`, bogus O*NET ids are nulled rather than propagated, judge scores clamp to the rubric
  length, `complete_answer` is true only at full marks, and the report writes its files.
- `cost_check` — accounting against a real HTTP server returning OpenRouter-shaped responses:
  costs match calls exactly, spend splits three ways (construction / solver / judge) with the
  judge's grade booked to the solver — not to construction — even though the judge shares the
  annotator's model id, and a re-run issues zero HTTP requests and bills $0 while reporting savings.
- `config_check` — every hoisted knob is honoured from the environment, reaches the objects
  built at each call site, and lands in the run config; no stray `os.environ` reads remain.
- `prompts_check` — the loader is faithful and fails loudly; the two verbatim prompts are
  byte-identical to the reference **source file**, not to a snapshot of this package.
- `fetch_check` / `discover_check` — the case list is well-formed and the split detector places
  boundaries correctly, including the answer-leak and truncation edge cases.
- `html_check` — the report is self-contained (no external requests), escapes model names, and
  renders the by-case score and cost line charts, and the detail-view case filter works.
- `cache_check` — the on-disk cache survives across separate processes (a second run makes zero
  API calls), same-file LLMs share one loaded cache, and the retry policy retries transient
  failures while failing fast on 400/401/404.
- `eval_check` — a failed solve is not sent to the judge: it scores 0 with no grading call.
- `skip_check` — incremental processing: a case already in a stage's output is skipped (zero
  LLM calls, not just cache hits), a new case in the same corpus is still processed, outputs
  merge instead of overwriting, and `--force` reprocesses everything.

Lint with `uv run ruff check src/ tests/`.

## Known-failure notes

[Reliability](reliability.md) — a provider returning an empty body as
a normal 200, cached as if valid. Worth reading before trusting a silent zero-yield case.

## Relationship to the reference implementation

What is copied verbatim, what is reimplemented, and what is new lives in its own page:
[What's new](whats-new.md).


## Layout

```
pyproject.toml            uv project
src/obcb/
  config.py               every setting, env-overridable, plus the run config
  llm.py                  async OpenRouter client, disk cache, JSON repair
  usage.py                token and USD accounting
  extractors.py           pluggable PDF -> markdown extractors
  prompts/                markdown prompt templates + validating loader
  onet.py                 O*NET WA / IWA / DWA taxonomy lookups
  fetch.py                download and split the corpus from the case list
  discover.py             find new candidates in the source journal
  extract.py              stage 1
  build.py                stage 2
  evaluate.py             stage 3
  report.py               stage 4
  html_report.py          scores.json -> a shareable HTML page
  cli.py                  click entry point
tests/                    offline, cost, config, prompts, fetch, discover, html, cache checks
docs/                     this documentation
data/                     case_list.json, O*NET taxonomy, fetched corpus, outputs
reference-paper-code/     the paper authors' release, unmodified (git-ignored)
```
