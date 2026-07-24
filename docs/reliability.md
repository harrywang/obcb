# Reliability: handling flaky providers

[← README](../README.md)

Over a full 54-case run the model API is called thousands of times, and providers are not perfectly
reliable — a request occasionally comes back **empty**, or **hangs**. Both surfaced on the first
full run, both had outsized effects because of how they interacted with the cache and the retry
policy, and both are now handled. This page is the record of what happened and why the current
behaviour is what it is.

## Incident: empty API responses cached as valid

A provider can return an **empty body as a normal HTTP 200**. Nothing in the pipeline treated
that as a failure, so it was memoised like any other response — and once cached, every later run
served it from disk without ever calling the API again. The damage was silent, permanent, and
looked exactly like a corpus-quality problem.

Found 2026-07-23, after the first full 54-case run. Fixed the same day.

### Symptom

The report showed **50 cases in the benchmark, not 54**. Four cases had been fetched, extracted,
profiled, and billed for construction, yet contributed zero questions:

```
jbca-advance-one-retreat-two
jbca-the-security-scandals-exercise
jbca-van-life-the-road
jbca-what-to-do-about
```

`build` reported each as `question extraction did not parse`, which reads like the model returned
malformed JSON, or like the teaching notes simply had no worked answers.

### Investigation

The obvious explanations were ruled out one at a time.

| Hypothesis | Check | Result |
|---|---|---|
| PDF extraction failed | chars/page, `instructor_failed_extraction` | Clean — 1,457–3,077 chars/page, no failures, 11k–52k chars of text |
| The teaching notes have no answers | Read all four notes | `what-to-do-about` has a full **"ANSWERS TO DISCUSSION QUESTIONS"** section with worked prose |
| The call errored out | `failed_calls` in the run record | **0** — no failures recorded anywhere |
| The model returned malformed JSON | Search the cache for unparseable question lists | **0** found |

The decisive clue was in the cache: **54 profile responses but only 50 extraction responses.**
Four extraction calls had produced nothing at all — yet no failure was recorded.

Calling the model directly for `what-to-do-about` settled it:

```
finish_reason: stop
completion_tokens: 13782  (reasoning_tokens: 4045)
content: non-empty
```

The content extracts fine. The case was never the problem.

Re-running `build --force` still failed, and still said `did not parse` — because the call never
happened. It was a **cache hit returning an empty string**.

### Root cause

Three separate gaps lined up so an empty response could do maximum damage:

1. **`llm._call` accepted an empty body.** `resp.choices[0].message.content or ""` turned `None`
   into `""` and returned it as a success. There was a guard for `not resp.choices`, but none for
   a present-but-empty message. All 50 such responses came back with `cost: 0.0` — the provider
   did not bill them, but the pipeline treated them as real.
2. **The cache memoised it.** `Cache.put` stored `""` like any other value. From then on every
   run was a *hit*, so the API was never called again and the failure could not heal itself. This
   is what made it permanent rather than transient.
3. **`build` conflated a failed call with a bad parse.** Both paths printed
   `did not parse`, so the logs actively pointed at the wrong culprit.

### Blast radius

**50 poisoned cache entries out of 2,099**, splitting cleanly by prompt size:

| Stage | Prompt size | Count | Effect |
|---|---|---|---|
| Question extraction | >6k tokens (8,171 / 9,957 / 10,531 / 17,601) | **4** | Four whole cases produced no questions |
| Rubric synthesis | <6k tokens | **46** | Questions silently dropped by the rubric gate |

The rubric row is the larger loss. Across the corpus 263 questions were extracted and 216 kept —
47 "dropped by the rubric quality gate". **46 of those 47 were empty responses, not quality
rejections.** Only one question was genuinely dropped for lacking a usable rubric.

So the benchmark should be roughly **262 questions across 54 cases**, not 216 across 50.

### What was *not* affected

Solve and grade were clean. All 648 result rows were checked: **zero empty model answers, zero
empty grading bodies**. No model was scored against a blank answer and no score came from a blank
judge response, so the published ranking (80.6% / 76.2% / 71.5% Standard) is unaffected.

This is worth stating plainly because an empty *grading* response would have been far worse than a
lost question: `parse_score("")` yields 0, so a model would have silently been handed a zero it
never earned.

### Fix

| Gap | Change |
|---|---|
| Empty body accepted | `llm._call` raises `RuntimeError` on a blank body. It is retryable, so it retries; if it persists the call is recorded as a genuine failure |
| Empty body memoised | `Cache.put` refuses to store a blank value, and `Cache.__init__` skips blank values when loading — so the 50 entries already on disk are treated as misses instead of needing a purge |
| Misleading log | `build` now distinguishes `the call failed` from `did not parse` |

Skipping blanks at load time was chosen over rewriting the cache file: it is non-destructive, and
a later real response for the same key simply appends and wins on reload.

### Regression tests

Four assertions in `tests/cache_check.py` (21 there, 194 across the suite):

- an empty cached body is not loaded (becomes a miss)
- a real cached body beside it still loads
- `put()` refuses to memoise an empty body
- `put()` still stores a real body

### Recovery

Done. `obcb build --force` refetched the 50 poisoned calls (everything else replayed from cache)
and incremental `evaluate` picked up only the new questions. **All 54 cases recovered; the
benchmark went from 216 questions to 293.** Net cost was ~$7.75, most of it absorbed by the cache
— the rebuild itself was **$0.18** (833 cached calls saved $14.78). The model ranking was
unchanged and no result row had an empty answer or empty grading.

A content review beforehand predicted that only `what-to-do-about` would recover and the other
three would legitimately yield nothing. **That prediction was wrong**, and the pipeline was right
to be trusted over it:

| Case | Prediction | Recovered |
|---|---|---|
| `what-to-do-about` | worked answers, should recover | 9 questions |
| `van-life` | multiple-choice only, won't survive | **12 questions** |
| `security-scandals-exercise` | role-play, no answers | **7 questions** |
| `advance-one-retreat-two` | inquiry-based, no answers | 3 questions |

The lesson: the surface features that were grepped (an MCQ block, a role-play framing) were real
but not the whole note. The gate decision belongs to the builder on a real response, not to a
human skimming for keywords.

## Aftershock: a 40-minute stall from the same flakiness

The first recovery attempt stalled for ~45 minutes with zero progress. Same root — a flaky
provider — different symptom: instead of returning an empty body, a request simply **hung**.

`OBCB_REQUEST_TIMEOUT` defaults to **600s**, and a timeout is retryable, so one hung request runs
`4 attempts × 600s ≈ 40 min` before the pipeline gives up on it — long enough to look frozen.
Re-running with `OBCB_REQUEST_TIMEOUT=240` capped a hang at ~16 min and the build finished in
2.4 minutes.

The default was left at 600 (generous headroom so a legitimately slow reasoning call is never
killed on a normal single-case run); 240 is documented in the README as the recommended override
for `-all` runs, alongside `OBCB_CONCURRENCY=24`.

## Lessons

- **A 200 is not a success.** Validate the shape of what came back, not just the status.
- **Never cache a failure.** A cache turns a transient fault into a permanent one, and hides it
  behind a hit that costs nothing and logs nothing.
- **Do not let two failure modes share one message.** `did not parse` sent the investigation
  toward the corpus for far longer than it should have.
- **A silent gap needs a visible counter.** The report now shows `50 / 54` cases with the shortfall
  named, rather than a bare `50` that looks intentional.
