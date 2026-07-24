"""Assert the response cache (cross-process reload, sharing) and the retry policy.

The property that matters for re-running a paper: invoke the pipeline twice with the
same models and the second run must make zero API calls. cost_check.py proves caching
within one process; this proves it across process boundaries, which is the real case
(you close the terminal, come back, run it again).
"""

from __future__ import annotations

import json
import sys

from obcb import config, llm

ok = True


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok
    ok = ok and cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")


# A stand-in for "a separate process": a fresh LLM object reads the same cache file.
CALLS = {"n": 0}


async def fake_call(self, prompt):  # _call is awaited, so the stub must be a coroutine
    CALLS["n"] += 1
    return f"answer to {prompt}", {"prompt_tokens": 10, "completion_tokens": 3, "cost": 0.001}


llm.LLM._call = fake_call  # type: ignore[method-assign]

cache_path = config.OUT_DIR / ".cache" / "test_cache.jsonl"
if cache_path.exists():
    cache_path.unlink()
llm.reset_caches()

PROMPTS = [f"question {i}" for i in range(5)]

# Run 1: fresh cache, every call is live.
a = llm.LLM("vendor/model-a", cache_path=cache_path)
out1 = a.map(PROMPTS, desc="run1")
n_after_run1 = CALLS["n"]
check("run 1 calls the API for every prompt", n_after_run1 == 5, f"{n_after_run1} calls")
check("run 1 returns an answer per prompt", all(o.startswith("answer to") for o in out1))

# Within one process, two LLMs on the same file share one Cache object (loaded once).
check("same-file LLMs share one cache object", a.cache is llm.get_cache(cache_path))

# Simulate a separate process: drop the in-memory registry so run 2 must reload from disk.
llm.reset_caches()

# Run 2: a brand-new object, same model, same cache file on disk. Zero calls.
b = llm.LLM("vendor/model-a", cache_path=cache_path)
check("run 2 got a fresh cache object (not the run-1 one)", b.cache is not a.cache)
out2 = b.map(PROMPTS, desc="run2")
check("run 2 loaded the cache written by run 1", len(b.cache.mem) >= 5, f"{len(b.cache.mem)} entries")
check("run 2 makes zero API calls", CALLS["n"] == n_after_run1, f"{CALLS['n'] - n_after_run1} new calls")
check("run 2 returns the same answers", out2 == out1)

# A different model shares the file but not the keys: it misses and calls.
c = llm.LLM("vendor/model-b", cache_path=cache_path)
c.map(PROMPTS, desc="run3")
check("a different model is a cache miss", CALLS["n"] == n_after_run1 + 5,
      f"{CALLS['n'] - n_after_run1} calls for the second model")

# ...but the first model still hits after the file grew.
d = llm.LLM("vendor/model-a", cache_path=cache_path)
before = CALLS["n"]
d.map(PROMPTS, desc="run4")
check("first model still hits after a second model was added", CALLS["n"] == before)

# A changed sampling parameter changes the key, so it is a miss - the cache never
# serves a response that was produced under different settings.
e = llm.LLM("vendor/model-a", params=config.LLMParams(temperature=0.9), cache_path=cache_path)
before = CALLS["n"]
e.map(PROMPTS[:1], desc="run5")
check("changed sampling params miss the cache", CALLS["n"] == before + 1)

cache_path.unlink(missing_ok=True)
llm.reset_caches()

# --- retry policy: transient failures retry, permanent ones fail fast ---
import httpx  # noqa: E402
import openai  # noqa: E402

req = httpx.Request("POST", "http://x")


def status_error(code: int) -> openai.APIStatusError:
    resp = httpx.Response(code, request=req)
    return openai.APIStatusError("boom", response=resp, body=None)


check("timeout is retryable", llm._is_retryable(openai.APITimeoutError(request=req)))
check("connection error is retryable", llm._is_retryable(openai.APIConnectionError(request=req)))
check("500 is retryable", llm._is_retryable(status_error(500)))
check("400 is NOT retryable", not llm._is_retryable(status_error(400)))
check("401 is NOT retryable", not llm._is_retryable(status_error(401)))
check("404 is NOT retryable", not llm._is_retryable(status_error(404)))
check("a plain RuntimeError is retryable (transient)", llm._is_retryable(RuntimeError("no choices")))

# --- empty responses must never be memoised -------------------------------------------
# A provider can return an empty body as a normal 200. Caching it made every later run a
# hit that skipped the API, silently dropping whole cases and questions downstream.
import asyncio  # noqa: E402

poisoned = config.OUT_DIR / ".cache" / "poison.jsonl"
poisoned.parent.mkdir(parents=True, exist_ok=True)
poisoned.write_text(
    json.dumps({"key": "k1", "value": "", "usage": {"cost": 0.0}}) + "\n"
    + json.dumps({"key": "k2", "value": "real answer", "usage": {"cost": 0.1}}) + "\n",
    encoding="utf-8",
)
llm.reset_caches()
cache = llm.get_cache(poisoned)
check("an empty cached body is not loaded (becomes a miss)", "k1" not in cache.mem)
check("a real cached body beside it still loads", cache.mem.get("k2") == "real answer")

asyncio.run(cache.put("k3", "   "))
check("put() refuses to memoise an empty body", "k3" not in cache.mem)
asyncio.run(cache.put("k4", "good"))
check("put() still stores a real body", cache.mem.get("k4") == "good")
poisoned.unlink(missing_ok=True)
llm.reset_caches()

print()
sys.exit(0 if ok else 1)
