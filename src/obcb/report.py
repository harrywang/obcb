"""Stage 4: aggregate results into the paper's two headline metrics.

Standard score (eq. 1-2): rubric-weighted fraction of criteria satisfied, averaged over
questions. Complete Answer score (eq. 3-4): the share of questions where every criterion
is satisfied. Uncertainty is a nonparametric bootstrap over questions at a percentile 95%
CI - the same procedure the paper reports. Resample count and seed live in config.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from statistics import mean

from rich.console import Console
from rich.table import Table

from . import config
from .jsonl import read_jsonl

console = Console()

def bootstrap_ci(values: list[float], b: int | None = None) -> tuple[float, float]:
    b = b if b is not None else config.BOOTSTRAP_B
    if len(values) < 2:
        return (values[0], values[0]) if values else (0.0, 0.0)
    rng = random.Random(config.BOOTSTRAP_SEED)
    n = len(values)
    means = sorted(mean(rng.choices(values, k=n)) for _ in range(b))
    return means[int(0.025 * b)], means[int(0.975 * b) - 1]


def _summarize(rows: list[dict]) -> dict:
    std = [r["standard_score"] for r in rows]
    cas = [float(r["complete_answer"]) for r in rows]
    lo_s, hi_s = bootstrap_ci(std)
    lo_c, hi_c = bootstrap_ci(cas)
    return {
        "n": len(rows),
        "standard": mean(std) if std else 0.0,
        "standard_ci": [lo_s, hi_s],
        "complete_answer": mean(cas) if cas else 0.0,
        "complete_answer_ci": [lo_c, hi_c],
    }


def _by(rows: list[dict], key, min_n: int = 1) -> dict[str, dict]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(str(key(row)), []).append(row)
    return {k: _summarize(v) for k, v in sorted(groups.items()) if len(v) >= min_n}


def _spend_summary() -> dict | None:
    """Roll up spend across every run: the on-disk log plus the current in-flight run.

    The current run's ledger is not written to ``usage.jsonl`` until the CLI's result
    callback fires, which is *after* ``report.run()`` — so reading only the log would
    omit the very run that produced this report (and leave a fresh, first-ever run with
    no cost at all). Folding in the live ledger closes that gap without double-counting:
    the current process's spend is in memory here, not yet on disk.
    """
    from . import usage

    runs = []
    if config.USAGE_LOG.exists():
        runs = [json.loads(x) for x in config.USAGE_LOG.read_text(encoding="utf-8").splitlines() if x]
    if not usage.LEDGER.is_empty():
        runs = runs + [usage.LEDGER.to_record("run")]
    if not runs:
        return None
    by_model: dict[str, float] = {}
    by_case: dict[str, float] = {}
    # {case: {model: {"solve_usd": x, "judge_usd": y}}} and {case: construction_usd}.
    # Classification is by *stage*, not model id: the two evaluation stages are solve and
    # grade; everything else (case profile, question extraction, rubric, metadata, O*NET)
    # is construction. This is robust even when the judge shares the annotator's model id.
    solve_grade: dict[str, dict[str, dict[str, float]]] = {}
    construction_by_case: dict[str, float] = {}
    seconds_by_case: dict[str, float] = {}
    for run_rec in runs:
        for model, stat in run_rec.get("by_model", {}).items():
            by_model[model] = by_model.get(model, 0.0) + stat["cost_usd"]
        for case, stat in run_rec.get("by_case", {}).items():
            by_case[case] = by_case.get(case, 0.0) + stat["cost_usd"]
            seconds_by_case[case] = seconds_by_case.get(case, 0.0) + stat.get("seconds", 0.0)
        for case, models in run_rec.get("by_case_model_stage", {}).items():
            for model, stages in models.items():
                for stage, stat in stages.items():
                    cost = stat["cost_usd"]
                    if stage == config.SOLVE_STAGE:
                        solve_grade.setdefault(case, {}).setdefault(
                            model, {"solve_usd": 0.0, "judge_usd": 0.0}
                        )["solve_usd"] += cost
                    elif stage == config.GRADE_STAGE:
                        solve_grade.setdefault(case, {}).setdefault(
                            model, {"solve_usd": 0.0, "judge_usd": 0.0}
                        )["judge_usd"] += cost
                    else:
                        construction_by_case[case] = construction_by_case.get(case, 0.0) + cost

    # Per case: shared construction + each model's own solve and the judge's grade of it.
    per_case = []
    total_construction = total_solver = total_judge = 0.0
    by_model_eval: dict[str, dict[str, float]] = {}  # {model: {solve_usd, judge_usd}}
    for case, total in sorted(by_case.items(), key=lambda kv: -kv[1]):
        construction = construction_by_case.get(case, 0.0)
        models = {
            m: {
                "solve_usd": v["solve_usd"],
                "judge_usd": v["judge_usd"],
                "total_usd": v["solve_usd"] + v["judge_usd"],
            }
            for m, v in solve_grade.get(case, {}).items()
        }
        solver_usd = sum(v["solve_usd"] for v in models.values())
        judge_usd = sum(v["judge_usd"] for v in models.values())
        total_construction += construction
        total_solver += solver_usd
        total_judge += judge_usd
        for m, v in models.items():
            agg = by_model_eval.setdefault(m, {"solve_usd": 0.0, "judge_usd": 0.0})
            agg["solve_usd"] += v["solve_usd"]
            agg["judge_usd"] += v["judge_usd"]
        per_case.append(
            {
                "case": case,
                "total_usd": total,
                "construction_usd": construction,
                "solver_usd": solver_usd,
                "judge_usd": judge_usd,
                "seconds": seconds_by_case.get(case, 0.0),
                "by_model": dict(sorted(models.items(), key=lambda kv: -kv[1]["total_usd"])),
            }
        )

    by_model_eval = {
        m: {**v, "total_usd": v["solve_usd"] + v["judge_usd"]}
        for m, v in sorted(by_model_eval.items(), key=lambda kv: -(kv[1]["solve_usd"] + kv[1]["judge_usd"]))
    }

    return {
        "runs": len(runs),
        "total_usd": sum(r["total"]["cost_usd"] for r in runs),
        "saved_usd": sum(r["total"]["saved_usd"] for r in runs),
        "prompt_tokens": sum(r["total"]["prompt_tokens"] for r in runs),
        "completion_tokens": sum(r["total"]["completion_tokens"] for r in runs),
        "construction_usd": total_construction,
        "solver_usd": total_solver,
        "judge_usd": total_judge,
        # Wall clock summed over runs, and total model (compute) time across all calls.
        "wall_seconds": sum(r.get("elapsed_s", 0.0) for r in runs),
        "model_seconds": sum(r["total"].get("seconds", 0.0) for r in runs),
        "by_model": by_model,
        "by_model_eval": by_model_eval,
        "per_case": per_case,
    }


def _case_list_index() -> dict[str, int]:
    """Map case slug -> its 1-based position in data/case_list.json.

    The report numbers cases by this index so a case keeps the same number across runs,
    no matter which subset was processed. Missing list (or slug) simply yields no number.
    """
    from . import fetch

    try:
        cases = fetch.load_case_list().get("cases", [])
    except Exception:  # noqa: BLE001 - numbering is cosmetic; never fail the report over it
        return {}
    return {c["slug"]: i for i, c in enumerate(cases, start=1) if c.get("slug")}


def _case_details(per_model: dict[str, list[dict]]) -> list[dict]:
    """Per-case, per-question detail: the builder's output plus how each model did.

    This is what powers the report's detail view — the extracted question, its reference
    solution, the checklist rubric, and each model's answer and score. Everything here
    already lives in the result rows (the benchmark fields ride along via ``**q`` in
    ``evaluate``); this just regroups it by case → question for display.
    """
    models = list(per_model)
    index_of = _case_list_index()
    # Index every model's rows by a stable question key so we can line them up.
    def qkey(r: dict) -> tuple:
        return (r["case_name"], r["question"])

    indexed = {m: {qkey(r): r for r in rows} for m, rows in per_model.items()}
    base = per_model[models[0]]  # question order/content is identical across models

    cases: dict[str, dict] = {}
    for r in base:
        c = cases.setdefault(
            r["case_name"],
            {
                "case_name": r["case_name"],
                "case_index": index_of.get(r["case_name"]),
                "case_title": r.get("case_title", r["case_name"]),
                "case_summary": r.get("case_summary", ""),
                "fictional_case": r.get("fictional_case"),
                "questions": [],
            },
        )
        answers = []
        for m in models:
            mr = indexed[m].get(qkey(r))
            if not mr:
                continue
            n = len(mr.get("grading_rubric", []))
            answers.append(
                {
                    "model": m,
                    "answer": mr.get("model_answer", ""),
                    "graded_score": mr.get("graded_score", 0),
                    "n_criteria": n,
                    "standard_score": mr.get("standard_score", 0.0),
                    "complete_answer": mr.get("complete_answer", 0),
                    "reasoning": mr.get("graded_score_reasoning", ""),
                }
            )
        c["questions"].append(
            {
                "question": r["question"],
                "solution": r.get("solution", ""),
                "grading_rubric": r.get("grading_rubric", []),
                "task_description": r.get("task_description", ""),
                "discipline": r.get("discipline", ""),
                "numerical": r.get("numerical"),
                "subjective": r.get("subjective"),
                "intermediate_work_activity": r.get("intermediate_work_activity"),
                "answers": answers,
            }
        )
    # Order by case-list position so numbering reads 1, 2, 3 … (unnumbered cases last).
    return sorted(cases.values(), key=lambda c: (c["case_index"] is None, c["case_index"] or 0))


def run(results_dir: Path | None = None, report_path: Path | None = None) -> dict:
    results_dir = results_dir or config.RESULTS_DIR
    report_path = report_path or config.REPORT_MD

    files = sorted(results_dir.glob("*.jsonl")) if results_dir.exists() else []
    if not files:
        raise SystemExit(f"No result files in {results_dir}. Run `obcb evaluate` first.")

    per_model = {}
    for path in files:
        rows = read_jsonl(path)
        if rows:
            per_model[rows[0]["model"]] = rows

    if not per_model:
        raise SystemExit(
            f"Result files in {results_dir} are empty. The benchmark has no questions — "
            "check that `obcb build` produced a non-empty benchmark.jsonl."
        )

    cases_detail = _case_details(per_model)
    # A case can be built (and billed for construction) yet contribute no questions: the
    # quality gates drop questions without an explicit reference solution or a usable
    # rubric, and a case can lose all of them. Report both counts so the difference is
    # visible rather than looking like cases silently went missing.
    built_cases = read_jsonl(config.CASES) if config.CASES.exists() else []
    built = [c["case_name"] for c in built_cases]
    scored = {c["case_name"] for c in cases_detail}
    # The configured extractor may be "auto"; report the concrete package(s) actually used,
    # recorded per case at extraction time, since that is what produced the text.
    used = sorted({c.get("case_metadata", {}).get("extractor") for c in built_cases} - {None})
    report = {
        "n_questions": len(next(iter(per_model.values()))),
        "n_cases": len(cases_detail),
        "n_cases_built": len(built) or len(cases_detail),
        "cases_without_questions": sorted(n for n in built if n not in scored),
        "extractor_used": ", ".join(used) or None,
        "judge_model": config.JUDGE_MODEL,
        # Embedded so a set of numbers can always be traced to what produced it.
        "config": config.resolved(),
        "models": {},
        # Per-case question / reference-solution / rubric detail, plus each model's
        # answer and score — the builder's output, for the report's detail view.
        "cases_detail": cases_detail,
    }
    for model, rows in per_model.items():
        report["models"][model] = {
            "overall": _summarize(rows),
            "by_discipline": _by(rows, lambda r: r["discipline"]),
            "by_case": _by(rows, lambda r: r["case_name"]),
            "by_question_type": {
                "numerical": _summarize([r for r in rows if r["numerical"]]),
                "non_numerical": _summarize([r for r in rows if not r["numerical"]]),
                "subjective": _summarize([r for r in rows if r["subjective"]]),
                "objective": _summarize([r for r in rows if not r["subjective"]]),
                "fictional_case": _summarize([r for r in rows if r.get("fictional_case")]),
                "real_case": _summarize([r for r in rows if not r.get("fictional_case")]),
            },
        }

    # Spend must be attached before scores.json is written, so the JSON and the HTML
    # rebuilt from it (obcb html) both carry cost — including the per-case breakdown.
    spend = _spend_summary()
    if spend:
        report["cost"] = spend

    config.SCORES_JSON.parent.mkdir(parents=True, exist_ok=True)
    config.SCORES_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")

    table = Table(title=f"Open Business Case Bench ({report['n_questions']} questions)")
    table.add_column("Model")
    table.add_column("Standard", justify="right")
    table.add_column("95% CI", justify="right")
    table.add_column("Complete Answer", justify="right")
    table.add_column("95% CI", justify="right")
    ranked = sorted(
        report["models"].items(), key=lambda kv: kv[1]["overall"]["standard"], reverse=True
    )
    for model, res in ranked:
        o = res["overall"]
        table.add_row(
            model,
            f"{o['standard']:.1%}",
            f"[{o['standard_ci'][0]:.1%}, {o['standard_ci'][1]:.1%}]",
            f"{o['complete_answer']:.1%}",
            f"[{o['complete_answer_ci'][0]:.1%}, {o['complete_answer_ci'][1]:.1%}]",
        )
    console.print(table)

    cfg = report["config"]
    lines = [
        "# Open Business Case Bench results",
        "",
        f"- Questions: {report['n_questions']}",
        f"- Judge: `{report['judge_model']}`",
        f"- Bootstrap: B={config.BOOTSTRAP_B}, percentile 95% CI, "
        f"seed {config.BOOTSTRAP_SEED}",
        f"- Extractor: `{cfg['extraction']['extractor']}` | "
        f"builder `{cfg['models']['builder']}` | annotator `{cfg['models']['annotator']}`",
        f"- Token budgets: solver {cfg['sampling']['solver_max_tokens']:,}, "
        f"judge {cfg['sampling']['judge_max_tokens']:,}, "
        f"temperature {cfg['sampling']['temperature']}",
        f"- Quality gates: min rubric criteria {cfg['quality_gates']['min_rubric_criteria']}, "
        f"min question/solution chars {cfg['quality_gates']['min_question_chars']}/"
        f"{cfg['quality_gates']['min_solution_chars']}",
        "",
        "- Prompts: "
        + ", ".join(f"`{k.replace('_PROMPT', '').lower()}`={v}" for k, v in cfg["prompts"].items()),
        "",
        "Full resolved configuration: `run_config.json`.",
        "",
        "## Overall",
        "",
        "| Model | Standard | 95% CI | Complete Answer | 95% CI |",
        "|---|---|---|---|---|",
    ]
    for model, res in ranked:
        o = res["overall"]
        lines.append(
            f"| `{model}` | {o['standard']:.1%} | "
            f"[{o['standard_ci'][0]:.1%}, {o['standard_ci'][1]:.1%}] | "
            f"{o['complete_answer']:.1%} | "
            f"[{o['complete_answer_ci'][0]:.1%}, {o['complete_answer_ci'][1]:.1%}] |"
        )

    lines += ["", "## By question type (Standard scoring)", ""]
    strata = ["numerical", "non_numerical", "subjective", "objective", "fictional_case", "real_case"]
    lines.append("| Model | " + " | ".join(s.replace("_", " ") for s in strata) + " |")
    lines.append("|---" * (len(strata) + 1) + "|")
    for model, res in ranked:
        cells = []
        for s in strata:
            st = res["by_question_type"][s]
            cells.append(f"{st['standard']:.1%} (n={st['n']})" if st["n"] else "-")
        lines.append(f"| `{model}` | " + " | ".join(cells) + " |")

    lines += ["", "## By discipline (Standard scoring)", ""]
    disciplines = sorted({d for res in report["models"].values() for d in res["by_discipline"]})
    lines.append("| Discipline | " + " | ".join(f"`{m}`" for m, _ in ranked) + " |")
    lines.append("|---" * (len(ranked) + 1) + "|")
    for discipline in disciplines:
        cells = []
        for model, res in ranked:
            st = res["by_discipline"].get(discipline)
            cells.append(f"{st['standard']:.1%} (n={st['n']})" if st else "-")
        lines.append(f"| {discipline} | " + " | ".join(cells) + " |")

    if spend:
        total = spend["total_usd"]
        lines += [
            "",
            "## Cost",
            "",
            f"- Total spend across all runs: **${total:.4f}**",
            f"- Tokens: {spend['prompt_tokens']:,} prompt + "
            f"{spend['completion_tokens']:,} output",
            f"- Time: {spend.get('wall_seconds', 0.0) / 60:.1f} min wall clock, "
            f"{spend.get('model_seconds', 0.0) / 60:.1f} min model time",
            f"- Saved by cache: ${spend['saved_usd']:.4f}",
            "",
            "| Category | Spend | Share |",
            "|---|---|---|",
            f"| Construction (extract + build) | ${spend['construction_usd']:.4f} | "
            f"{(spend['construction_usd'] / total * 100 if total else 0):.0f}% |",
            f"| Solver (answers under test) | ${spend['solver_usd']:.4f} | "
            f"{(spend['solver_usd'] / total * 100 if total else 0):.0f}% |",
            f"| Judge (grading) | ${spend['judge_usd']:.4f} | "
            f"{(spend['judge_usd'] / total * 100 if total else 0):.0f}% |",
            f"| **Total** | **${total:.4f}** | |",
            "",
            "| Model | Solve | Judge | Total |",
            "|---|---|---|---|",
        ]
        for model, v in spend.get("by_model_eval", {}).items():
            lines.append(
                f"| `{model}` | ${v['solve_usd']:.4f} | ${v['judge_usd']:.4f} | "
                f"${v['total_usd']:.4f} |"
            )

    lines += [
        "",
        "## Caveat",
        "",
        "These cases are openly published on the web, so pre-training contamination is",
        "expected. Treat these numbers as a pipeline check, not as a capability measurement.",
        "",
    ]

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")

    from . import html_report

    html_path = html_report.write(report, config.REPORT_HTML)

    console.print(
        f"\nWrote [bold]{report_path}[/], [bold]{html_path}[/], "
        f"and [bold]{config.SCORES_JSON}[/]"
    )
    return report
