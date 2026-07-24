"""Async OpenRouter client with an on-disk cache.

Replaces the reference pipeline's DataDreamer dependency. The two properties that
mattered there are preserved: every prompt/response pair is memoised so a rerun
resumes instead of re-billing, and prompts within a stage are issued concurrently.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from typing import IO, Any, Sequence

import openai
from openai import AsyncOpenAI
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from . import config, usage

FAILED = "__OBCB_FAILED__"


def _is_retryable(exc: BaseException) -> bool:
    """Retry transient failures; fail fast on ones a retry cannot fix.

    A 400 (bad request), 401/403 (auth), 404 or 422 will never succeed on retry, so
    burning four attempts plus backoff on them is pure waste. Only timeouts, rate limits,
    connection drops, and 5xx are worth retrying.
    """
    if isinstance(
        exc,
        (
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.RateLimitError,
            openai.InternalServerError,
        ),
    ):
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code >= 500
    if isinstance(exc, openai.APIError):
        return False  # other API errors (400/401/403/404/422) — not retryable
    return True  # non-API (our RuntimeError, transient asyncio) — retry


class Cache:
    """Append-only JSONL cache keyed by a hash of (model, prompt, sampling params).

    Each entry stores the response text plus the usage it originally cost, so a
    later cache hit can report what it saved instead of showing a blank. The append
    handle is opened once and kept open, rather than re-opened per write.
    """

    def __init__(self, path: Path):
        self.path = path
        self.mem: dict[str, str] = {}
        self.usage: dict[str, dict] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # tolerate a torn final line from an interrupted run
                # Never serve an empty body. Providers occasionally return one as a normal
                # 200, and older runs cached those verbatim — which silently dropped cases
                # and questions on every subsequent run, since a hit skips the API entirely.
                # Skipping them here turns the poisoned entry back into a cache miss.
                if not (rec.get("value") or "").strip():
                    continue
                self.mem[rec["key"]] = rec["value"]
                if rec.get("usage"):
                    self.usage[rec["key"]] = rec["usage"]
        self._lock = asyncio.Lock()
        self._handle: IO[str] | None = None

    def _fh(self) -> IO[str]:
        if self._handle is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a", encoding="utf-8")
        return self._handle

    async def put(self, key: str, value: str, billed: dict | None = None) -> None:
        if not (value or "").strip():
            return  # never memoise an empty body (see the loader) — retry it instead
        async with self._lock:
            self.mem[key] = value
            if billed:
                self.usage[key] = billed
            rec = {"key": key, "value": value, "usage": billed or {}}
            handle = self._fh()
            handle.write(json.dumps(rec, ensure_ascii=False) + "\n")
            handle.flush()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


# One Cache per file path, shared across every LLM in the process. Without this, each
# LLM (builder, annotator, every solver, the judge) re-parses the whole cache file on
# construction. Sharing also means a write by one model is visible to the rest.
_CACHES: dict[str, Cache] = {}


def get_cache(path: Path) -> Cache:
    key = str(path)
    cache = _CACHES.get(key)
    if cache is None:
        cache = _CACHES[key] = Cache(path)
    return cache


def reset_caches() -> None:
    """Drop the process cache registry (closing handles). Used by tests to simulate a
    fresh process so on-disk reload can be exercised."""
    for cache in _CACHES.values():
        cache.close()
    _CACHES.clear()


class LLM:
    def __init__(
        self,
        model: str,
        *,
        params: config.LLMParams | None = None,
        cache_path: Path | None = None,
        concurrency: int | None = None,
    ):
        self.model = model
        self.params = params or config.LLMParams()
        self.concurrency = concurrency or config.CONCURRENCY
        self.cache = get_cache(cache_path or config.CACHE_PATH)
        self._client: AsyncOpenAI | None = None
        # Resolve eagerly: a missing key should fail here with a clean message rather
        # than surface as a traceback from inside the asyncio task group.
        self._api_key = config.api_key()

    def _key(self, prompt: str) -> str:
        payload = json.dumps(
            {
                "model": self.model,
                "prompt": prompt,
                "temperature": self.params.temperature,
                "top_p": self.params.top_p,
                "max_tokens": self.params.max_tokens,
                "extra": self.params.extra,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def _ensure_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=config.BASE_URL,
                timeout=config.REQUEST_TIMEOUT,
                max_retries=0,  # tenacity owns retries so backoff is visible and bounded
            )
        return self._client

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        reraise=True,
    )
    async def _call(self, prompt: str) -> tuple[str, dict]:
        resp = await self._ensure_client().chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self.params.temperature,
            top_p=self.params.top_p,
            max_tokens=self.params.max_tokens,
            **self.params.extra,
        )
        if not resp.choices:
            raise RuntimeError(f"{self.model} returned no choices")
        content = resp.choices[0].message.content or ""
        # An empty body is never a usable answer for any stage here, but it arrives as a
        # normal 200 (finish_reason "stop"), so nothing else would catch it. Left alone it
        # gets cached as if it were a real response, permanently poisoning that prompt and
        # silently dropping the case downstream. Raise instead: it is retryable, and if it
        # persists the call is counted as failed rather than mistaken for a bad parse.
        if not content.strip():
            raise RuntimeError(f"{self.model} returned an empty response")
        prompt_tokens, completion_tokens, cost = usage.parse_usage(getattr(resp, "usage", None))
        billed = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost": cost,
        }
        return content, billed

    async def _one(
        self, prompt: str, sem: asyncio.Semaphore, stage: str, case: str, record_as: str
    ) -> str:
        # ``record_as`` is the model the *cost* is attributed to, which can differ from
        # the model doing the work: the judge grades a solver's answer, but that spend
        # belongs to the (case, solver) pair, not to the judge. Caching still keys on the
        # real model (self.model) via _key — grading is the judge's work and caches once.
        key = self._key(prompt)
        hit = self.cache.mem.get(key)
        if hit is not None:
            prior = self.cache.usage.get(key, {})
            usage.LEDGER.record(
                record_as,
                stage,
                case=case,
                prompt_tokens=prior.get("prompt_tokens", 0),
                completion_tokens=prior.get("completion_tokens", 0),
                cost=prior.get("cost"),
                cached=True,
            )
            return hit
        async with sem:
            t0 = time.monotonic()
            try:
                out, billed = await self._call(prompt)
            except Exception as exc:  # noqa: BLE001 - one bad row must not kill the stage
                usage.LEDGER.record(
                    record_as, stage, case=case, failed=True, seconds=time.monotonic() - t0
                )
                return f"{FAILED}{type(exc).__name__}: {exc}"
            elapsed = time.monotonic() - t0
        usage.LEDGER.record(
            record_as,
            stage,
            case=case,
            prompt_tokens=billed["prompt_tokens"],
            completion_tokens=billed["completion_tokens"],
            cost=billed["cost"],
            seconds=elapsed,
        )
        await self.cache.put(key, out, billed)
        return out

    async def _gather(
        self, prompts: Sequence[str], desc: str, cases: Sequence[str], record_as: str
    ) -> list[str]:
        sem = asyncio.Semaphore(self.concurrency)
        results: list[str | None] = [None] * len(prompts)
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            transient=True,
        ) as progress:
            task = progress.add_task(f"{desc} [{self.model}]", total=len(prompts))

            async def run(i: int, p: str) -> None:
                results[i] = await self._one(p, sem, desc, cases[i], record_as)
                progress.advance(task)

            await asyncio.gather(*(run(i, p) for i, p in enumerate(prompts)))
        return [r or "" for r in results]

    def map(
        self,
        prompts: Sequence[str],
        desc: str = "prompting",
        cases: Sequence[str] | None = None,
        record_as: str | None = None,
    ) -> list[str]:
        """Run every prompt, returning outputs in input order. Cached rows cost nothing.

        ``cases`` is an optional parallel list tagging each prompt with the case it
        belongs to, so cost can be attributed per paper. Omit it for case-less calls.

        ``record_as`` overrides which model the cost is booked against — used so the
        judge's grading spend lands on the solver whose answer it graded, not on the
        judge. Defaults to this LLM's own model.
        """
        if not prompts:
            return []
        labels = list(cases) if cases is not None else [""] * len(prompts)
        if len(labels) != len(prompts):
            raise ValueError("cases must be the same length as prompts")
        return asyncio.run(self._gather(prompts, desc, labels, record_as or self.model))


def failed(output: str) -> bool:
    return output.startswith(FAILED)


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def parse_json(output: str) -> Any | None:
    """Best-effort JSON extraction from a chat completion.

    Handles bare JSON, fenced blocks, and prose wrapped around a single object or array.
    Returns None when nothing parses, so callers can decide whether to retry or drop.
    """
    if not output or failed(output):
        return None
    text = _FENCE.sub("", output).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None
