"""Stage 3: run solver models over the benchmark and grade them with a fixed judge.

Single-turn, no tools, no retrieval, full case narrative in one prompt - matching the
paper's protocol (SI E2). The judge model is held constant across solvers so scores stay
comparable, exactly as in the reference ``evaluate_models.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

from rich.console import Console

from . import config, prompts
from .jsonl import read_jsonl, write_jsonl
from .llm import LLM, failed

console = Console()


def parse_score(output: str) -> tuple[int, str]:
    """Score extraction copied from the reference grade_model_answer_against_rubric."""
    if not output or not output.strip():
        return 0, output

    match = re.search(r"<<\s*([0-9]+)\s*>>", output)
    if match is None:
        match = re.search(r"Final\s*Score\s*:\s*\*?\s*([0-9]+)(?:\D|$)", output, flags=re.IGNORECASE)
    if match is None:
        return 0, output
    try:
        return int(match.group(1)), output
    except ValueError:
        return 0, output


def _qkey(q: dict) -> tuple[str, str]:
    """Stable identity for a benchmark question: which case, which prompt."""
    return (q["case_name"], q["question"])


def run(
    models: list[str] | None = None,
    benchmark_path: Path | None = None,
    cases_path: Path | None = None,
    results_dir: Path | None = None,
    limit: int | None = None,
    only: set[str] | None = None,
    force: bool = False,
) -> dict[str, list[dict]]:
    models = models or config.DEFAULT_SOLVERS
    benchmark_path = benchmark_path or config.BENCHMARK
    cases_path = cases_path or config.CASES
    results_dir = results_dir or config.RESULTS_DIR

    if not benchmark_path.exists():
        raise SystemExit(f"{benchmark_path} not found. Run `obcb build` first.")

    questions = read_jsonl(benchmark_path)
    # Scope to the requested cases (the pipeline passes the run's slugs); benchmark.jsonl
    # holds every question ever built, but a run evaluates only its target cases.
    if only is not None:
        questions = [q for q in questions if q["case_name"] in only]
    if limit:
        questions = questions[:limit]
    cases = {c["case_name"]: c for c in read_jsonl(cases_path)}

    console.print(f"Evaluating {len(models)} model(s) on {len(questions)} question(s)\n")

    judge = LLM(config.JUDGE_MODEL, params=config.LLMParams(max_tokens=config.JUDGE_MAX_TOKENS))
    all_results: dict[str, list[dict]] = {}

    for model in models:
        out_path = results_dir / f"{model.replace('/', '_')}.jsonl"

        # Skip questions this model has already answered (unless --force). Each model has
        # its own results file, so a newly added solver still evaluates every question.
        prior = [] if force else read_jsonl(out_path) if out_path.exists() else []
        done = {_qkey(r) for r in prior}
        todo = [q for q in questions if _qkey(q) not in done]
        if not todo:
            console.print(f"[dim]{model}: all {len(questions)} question(s) already evaluated[/]\n")
            all_results[model] = prior
            continue
        if done:
            console.print(f"[dim]{model}: {len(done)} already done, evaluating {len(todo)} new[/]")

        solver = LLM(model, params=config.LLMParams(max_tokens=config.SOLVER_MAX_TOKENS))

        solve_prompts = [
            prompts.SOLVE_PROMPT.format(
                question=q["question"],
                case_clean_text=cases[q["case_name"]]["case_clean_text"],
            )
            for q in todo
        ]
        answers = solver.map(
            solve_prompts, desc=config.SOLVE_STAGE, cases=[q["case_name"] for q in todo]
        )

        # Grade only answers that actually came back. A failed solve scores 0 without a
        # judge call — grading the FAILED sentinel would waste a paid call on a non-answer.
        gradable = [i for i, a in enumerate(answers) if not failed(a)]
        grade_prompts = [
            prompts.GRADE_PROMPT.format(
                question=todo[i]["question"],
                model_answer=answers[i],
                grading_rubric_list="\n".join(
                    f"{n + 1}. {c}" for n, c in enumerate(todo[i]["grading_rubric"])
                ),
                solution=todo[i]["solution"],
                case_summary=cases[todo[i]["case_name"]]["case_summary"],
            )
            for i in gradable
        ]
        graded = judge.map(
            grade_prompts,
            desc=config.GRADE_STAGE,
            cases=[todo[i]["case_name"] for i in gradable],
            record_as=model,  # book the judge's grading spend to the solver it graded
        )
        gradings = [""] * len(todo)
        for i, grading in zip(gradable, graded):
            gradings[i] = grading

        new_rows = []
        for q, answer, grading in zip(todo, answers, gradings):
            n_criteria = len(q["grading_rubric"])
            raw_score, reasoning = parse_score(grading)
            # Reference clamps the judge's total into [0, len(rubric)] before normalising.
            score = min(max(0, raw_score), n_criteria)
            new_rows.append(
                {
                    **q,
                    "model": model,
                    "model_answer": answer,
                    "model_answer_failed": failed(answer),
                    "graded_score": score,
                    "graded_score_reasoning": reasoning,
                    "grading_failed": failed(grading),
                    "standard_score": score / n_criteria if n_criteria else 0.0,
                    "complete_answer": int(n_criteria > 0 and score == n_criteria),
                }
            )

        rows = prior + new_rows
        write_jsonl(out_path, rows)
        all_results[model] = rows

        n_bad = sum(r["model_answer_failed"] or r["grading_failed"] for r in new_rows)
        mean_std = sum(r["standard_score"] for r in rows) / len(rows) if rows else 0.0
        mean_cas = sum(r["complete_answer"] for r in rows) / len(rows) if rows else 0.0
        console.print(
            f"[bold]{model}[/]: Standard {mean_std:.1%} | Complete Answer {mean_cas:.1%}"
            + (f" [yellow]({n_bad} API failure(s))[/]" if n_bad else "")
        )
        console.print(f"  -> {out_path}\n")

    return all_results
