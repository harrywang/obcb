"""Token and cost accounting.

OpenRouter returns the authoritative per-request USD cost inline on every chat
completion (``usage.cost``), so we never have to maintain a price list or make a
second call. We read it defensively: any OpenAI-compatible endpoint works, and one
that omits the field is reported as "cost unavailable" rather than silently zero.

Cached calls are tracked separately. A re-run of a stage costs nothing but still
consumed tokens the first time, so the ledger reports both what this run billed and
what the cache saved.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.table import Table


@dataclass
class Stat:
    calls: int = 0
    cached_calls: int = 0
    failed_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    saved_usd: float = 0.0  # cost avoided by cache hits
    cost_unknown_calls: int = 0  # billed calls whose response carried no cost field
    # Summed model time. Calls run concurrently, so this is compute time spent on this
    # key, not wall clock — it can exceed the run's elapsed time. Cache hits add ~0.
    seconds: float = 0.0

    def merge(self, other: Stat) -> None:
        for key, value in asdict(other).items():
            setattr(self, key, getattr(self, key) + value)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class Ledger:
    """Accumulates usage for one CLI invocation, keyed by (model, stage, case).

    The case dimension is what makes per-paper cost possible: extract and build run
    once per case (shared construction cost), while solve and grade run once per
    (case, model). ``case`` is "" for calls not tied to a specific case.
    """

    entries: dict[tuple[str, str, str], Stat] = field(default_factory=dict)
    # Wall clock for the whole invocation, started when the ledger is created (import time).
    started_at: float = field(default_factory=time.monotonic)

    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at

    def record(
        self,
        model: str,
        stage: str,
        *,
        case: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost: float | None = None,
        cached: bool = False,
        failed: bool = False,
        seconds: float = 0.0,
    ) -> None:
        stat = self.entries.setdefault((model, stage, case), Stat())
        stat.calls += 1
        stat.seconds += seconds
        if failed:
            stat.failed_calls += 1
            return
        stat.prompt_tokens += prompt_tokens
        stat.completion_tokens += completion_tokens
        if cached:
            stat.cached_calls += 1
            stat.saved_usd += cost or 0.0
        else:
            stat.cost_usd += cost or 0.0
            if cost is None:
                stat.cost_unknown_calls += 1

    def totals(self) -> Stat:
        total = Stat()
        for stat in self.entries.values():
            total.merge(stat)
        return total

    def by(self, index: int) -> dict[str, Stat]:
        """Aggregate by model (0), stage (1), or case (2), ignoring blank keys."""
        out: dict[str, Stat] = {}
        for key, stat in self.entries.items():
            k = key[index]
            if index == 2 and not k:
                continue  # case-less calls are not per-paper cost
            out.setdefault(k, Stat()).merge(stat)
        return out

    def by_case_model(self) -> dict[str, dict[str, Stat]]:
        """Per-case, then per-model: {case: {model: Stat}}. Case-less calls dropped."""
        out: dict[str, dict[str, Stat]] = {}
        for (model, _stage, case), stat in self.entries.items():
            if not case:
                continue
            out.setdefault(case, {}).setdefault(model, Stat()).merge(stat)
        return out

    def by_case_model_stage(self) -> dict[str, dict[str, dict[str, Stat]]]:
        """Per-case, per-model, per-stage: {case: {model: {stage: Stat}}}.

        The stage dimension is what lets the report split each case into construction
        (build stages) vs. solve vs. grade without guessing from model ids.
        """
        out: dict[str, dict[str, dict[str, Stat]]] = {}
        for (model, stage, case), stat in self.entries.items():
            if not case:
                continue
            out.setdefault(case, {}).setdefault(model, {}).setdefault(stage, Stat()).merge(stat)
        return out

    def is_empty(self) -> bool:
        return not self.entries

    # -- reporting ----------------------------------------------------------------

    def table(self, title: str = "Usage and cost") -> Table:
        table = Table(title=title)
        # fold rather than ellipsize: a truncated model id is useless for cost attribution
        table.add_column("Model", overflow="fold")
        table.add_column("Stage", overflow="fold")
        table.add_column("Calls", justify="right")
        table.add_column("Cached", justify="right")
        table.add_column("Prompt tok", justify="right")
        table.add_column("Output tok", justify="right")
        table.add_column("Cost USD", justify="right")

        # Aggregate the case dimension out so the console stays per (model, stage);
        # per-case detail lives in the run record and the HTML report.
        by_ms: dict[tuple[str, str], Stat] = {}
        for (model, stage, _case), stat in self.entries.items():
            by_ms.setdefault((model, stage), Stat()).merge(stat)
        for (model, stage), s in sorted(by_ms.items(), key=lambda kv: -kv[1].cost_usd):
            table.add_row(
                model,
                stage,
                str(s.calls),
                str(s.cached_calls) if s.cached_calls else "-",
                f"{s.prompt_tokens:,}",
                f"{s.completion_tokens:,}",
                f"${s.cost_usd:.4f}" + ("*" if s.cost_unknown_calls else ""),
            )

        t = self.totals()
        table.add_section()
        table.add_row(
            "[bold]TOTAL[/]",
            "",
            f"[bold]{t.calls}[/]",
            f"[bold]{t.cached_calls or '-'}[/]",
            f"[bold]{t.prompt_tokens:,}[/]",
            f"[bold]{t.completion_tokens:,}[/]",
            f"[bold]${t.cost_usd:.4f}[/]",
        )
        return table

    def print_summary(self, console: Console | None = None) -> None:
        console = console or Console()
        if self.is_empty():
            return
        console.print()
        console.print(self.table())
        t = self.totals()
        notes = [
            f"{self.elapsed_s() / 60:.1f} min wall clock | {t.seconds / 60:.1f} min model time"
        ]
        if t.cached_calls:
            notes.append(f"{t.cached_calls} cached call(s) saved ${t.saved_usd:.4f}")
        if t.failed_calls:
            notes.append(f"[yellow]{t.failed_calls} failed call(s)[/]")
        if t.cost_unknown_calls:
            notes.append(
                f"[yellow]*{t.cost_unknown_calls} call(s) returned no cost field; "
                "totals are a lower bound[/]"
            )
        if notes:
            console.print("  " + " | ".join(notes))

    def to_record(self, command: str) -> dict:
        t = self.totals()
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "command": command,
            "elapsed_s": round(self.elapsed_s(), 1),  # wall clock for this invocation
            "total": asdict(t),
            "by_model": {k: asdict(v) for k, v in self.by(0).items()},
            "by_stage": {k: asdict(v) for k, v in self.by(1).items()},
            "by_case": {k: asdict(v) for k, v in self.by(2).items()},
            "by_case_model": {
                case: {model: asdict(stat) for model, stat in models.items()}
                for case, models in self.by_case_model().items()
            },
            "by_case_model_stage": {
                case: {
                    model: {stage: asdict(stat) for stage, stat in stages.items()}
                    for model, stages in models.items()
                }
                for case, models in self.by_case_model_stage().items()
            },
        }

    def save(self, path: Path, command: str) -> None:
        """Append this run to the usage log. One JSON object per line."""
        if self.is_empty():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(self.to_record(command), ensure_ascii=False) + "\n")


# One ledger per CLI invocation.
LEDGER = Ledger()


def parse_usage(raw) -> tuple[int, int, float | None]:
    """Pull (prompt_tokens, completion_tokens, cost) out of a completion response.

    ``cost`` is None when the endpoint does not report one, which is how we
    distinguish "free" from "unknown".
    """
    if raw is None:
        return 0, 0, None
    prompt = getattr(raw, "prompt_tokens", 0) or 0
    completion = getattr(raw, "completion_tokens", 0) or 0
    cost = getattr(raw, "cost", None)
    if cost is None:
        # The OpenAI SDK keeps provider-specific fields in model_extra.
        extra = getattr(raw, "model_extra", None) or {}
        cost = extra.get("cost")
    return int(prompt), int(completion), float(cost) if cost is not None else None
