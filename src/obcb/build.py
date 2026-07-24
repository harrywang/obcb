"""Stage 2: cases_raw.jsonl -> cases.jsonl + benchmark.jsonl.

Implements Methods "Benchmark construction": profile each case, extract question /
reference-solution pairs from the instructor solution only, synthesise an equally
weighted checklist rubric per question, then annotate question-type metadata,
discipline, and O*NET work-activity tags.

The quality gate mirrors the paper: an instance is dropped unless it carries an explicit
reference solution and a usable rubric.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from rich.console import Console

from . import config, onet, prompts
from .jsonl import read_jsonl, write_jsonl
from .llm import LLM, failed, parse_json

console = Console()


def _as_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return default


def _generate(llm: LLM, items: list[dict], prompt_fn: Callable[[dict], str], desc: str) -> list[str]:
    """Render one prompt per item, tag each with its case, and map through the LLM.

    Every item carries ``case_name``; this keeps the per-stage boilerplate (build the
    prompt list, build the parallel case list, call map) in one place so the two lists
    cannot drift out of sync.
    """
    return llm.map([prompt_fn(it) for it in items], desc=desc, cases=[it["case_name"] for it in items])


def profile_cases(cases: list[dict], llm: LLM) -> list[dict]:
    outputs = _generate(
        llm,
        cases,
        lambda c: prompts.CASE_PROFILE_PROMPT.format(
            case_text=c["case_clean_text"], instructor_text=c["instructor_clean_text"]
        ),
        "case profiles",
    )

    profiled = []
    for case, out in zip(cases, outputs):
        data = parse_json(out) or {}
        if not isinstance(data, dict):
            data = {}
        objectives = data.get("case_learning_objectives") or []
        profiled.append(
            {
                **case,
                "case_title": data.get("case_title") or case["case_name"],
                "case_summary": data.get("case_summary", ""),
                "case_learning_objectives": [str(o) for o in objectives if str(o).strip()],
                "case_fictional_case": _as_bool(data.get("fictional_case"), True),
                "case_fictional_reasoning": data.get("fictional_reasoning", ""),
            }
        )
        if not data:
            why = "the call failed" if failed(out) else "did not parse"
            console.print(f"[yellow]{case['case_name']}: case profile {why}[/]")
    return profiled


def extract_questions(cases: list[dict], llm: LLM) -> list[dict]:
    outputs = _generate(
        llm,
        cases,
        lambda c: prompts.EXTRACT_QUESTIONS_PROMPT.format(
            case_text=c["case_clean_text"], instructor_text=c["instructor_clean_text"]
        ),
        "question extraction",
    )

    items: list[dict] = []
    for case, out in zip(cases, outputs):
        parsed = parse_json(out)
        if not isinstance(parsed, list):
            # Distinguish a failed API call from a genuinely malformed body: conflating
            # them once made four cases look like corpus problems when the calls had
            # simply come back empty.
            why = "the call failed" if failed(out) else "did not parse"
            console.print(f"[yellow]{case['case_name']}: question extraction {why}[/]")
            continue
        kept = 0
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            question = str(entry.get("question", "")).strip()
            solution = str(entry.get("solution", "")).strip()
            # Quality gate: no explicit reference solution, no benchmark instance.
            if (
                len(question) < config.MIN_QUESTION_CHARS
                or len(solution) < config.MIN_SOLUTION_CHARS
            ):
                continue
            items.append(
                {
                    "case_name": case["case_name"],
                    "question": question,
                    "solution": solution,
                    "task_description": str(entry.get("task_description", "")).strip(),
                }
            )
            kept += 1
        console.print(f"  {case['case_name']}: {kept} question(s)")
    return items


def add_rubrics(items: list[dict], llm: LLM) -> list[dict]:
    outputs = _generate(
        llm,
        items,
        lambda i: prompts.RUBRIC_PROMPT.format(question=i["question"], solution=i["solution"]),
        "rubrics",
    )

    kept = []
    for item, out in zip(items, outputs):
        parsed = parse_json(out)
        criteria = (
            [str(c).strip() for c in parsed if str(c).strip()] if isinstance(parsed, list) else []
        )
        if len(criteria) < config.MIN_RUBRIC_CRITERIA:
            console.print(f"[yellow]dropping (no usable rubric): {item['question'][:70]}...[/]")
            continue
        kept.append({**item, "grading_rubric": criteria})
    return kept


def add_metadata(items: list[dict], llm: LLM) -> list[dict]:
    tax = onet.load()
    iwa_menu = tax.iwa_list()
    discipline_menu = "\n".join(f"  - {d}" for d in config.DISCIPLINES)

    outputs = _generate(
        llm,
        items,
        lambda i: prompts.METADATA_PROMPT.format(
            question=i["question"],
            solution=i["solution"],
            discipline_list=discipline_menu,
            iwa_list=iwa_menu,
        ),
        "metadata + O*NET",
    )

    staged = []
    for item, out in zip(items, outputs):
        data = parse_json(out)
        data = data if isinstance(data, dict) else {}
        discipline = str(data.get("discipline", "")).strip()
        if discipline not in config.DISCIPLINES:
            discipline = "Other"
        numerical = _as_bool(data.get("numerical"))
        staged.append(
            {
                **item,
                "discipline": discipline,
                "numerical": numerical,
                "primarily_numerical": _as_bool(data.get("primarily_numerical")) and numerical,
                "subjective": _as_bool(data.get("subjective"), True),
                "subjective_reasoning": str(data.get("subjective_reasoning", "")),
                "_iwa_id": str(data.get("intermediate_work_activity_id", "")).strip(),
            }
        )

    # Second pass for the finest taxonomy level, restricted to the chosen IWA's children.
    # dwa_targets holds references into `staged`, so writing `_dwa_id` back onto a target
    # updates the corresponding staged item directly — no id()-keyed side table needed.
    dwa_targets = [i for i in staged if i["_iwa_id"] in tax.iwas]
    dwa_outputs = _generate(
        llm,
        dwa_targets,
        lambda i: prompts.DWA_PROMPT.format(
            question=i["question"], dwa_list=tax.dwa_list(i["_iwa_id"])
        ),
        "detailed work activities",
    )
    for target, out in zip(dwa_targets, dwa_outputs):
        data = parse_json(out)
        target["_dwa_id"] = (
            str(data.get("detailed_work_activity_id", "")).strip()
            if isinstance(data, dict)
            else None
        )

    final = []
    for item in staged:
        iwa_id = item.pop("_iwa_id")
        dwa_id = item.pop("_dwa_id", None)
        tags = tax.resolve(iwa_id or None, dwa_id)
        if tags["intermediate_work_activity_id"] is None:
            console.print(f"[yellow]unmapped O*NET IWA: {item['question'][:60]}...[/]")
        final.append({**item, **tags})
    return final


def run(
    cases_path: Path | None = None,
    cases_out: Path | None = None,
    benchmark_out: Path | None = None,
    limit: int | None = None,
    only: set[str] | None = None,
    force: bool = False,
) -> list[dict]:
    cases_path = cases_path or config.CASES_RAW
    cases_out = cases_out or config.CASES
    benchmark_out = benchmark_out or config.BENCHMARK

    if not cases_path.exists():
        raise SystemExit(f"{cases_path} not found. Run `obcb extract` first.")
    all_cases = read_jsonl(cases_path)
    # Scope to the requested cases: cases_raw.jsonl accumulates every case ever extracted,
    # but a run targets a specific set, so build only those (the pipeline passes the run's slugs).
    if only is not None:
        all_cases = [c for c in all_cases if c["case_name"] in only]
    if limit:
        all_cases = all_cases[:limit]

    # Skip cases already present in cases.jsonl (unless --force), and merge the new work
    # into the existing outputs rather than overwriting them.
    existing_cases = {c["case_name"]: c for c in read_jsonl(cases_out)} if cases_out.exists() else {}
    existing_rows = read_jsonl(benchmark_out) if benchmark_out.exists() else []
    todo = all_cases if force else [c for c in all_cases if c["case_name"] not in existing_cases]
    for name in (c["case_name"] for c in all_cases if c["case_name"] in existing_cases and not force):
        console.print(f"[dim]{name}: already built, skipping[/]")

    if not todo:
        console.print("All requested cases already built; nothing to do.")
        return existing_rows

    console.print(f"Building benchmark from {len(todo)} case(s)\n")

    builder = LLM(
        config.BUILDER_MODEL, params=config.LLMParams(max_tokens=config.BUILDER_MAX_TOKENS)
    )
    annotator = LLM(
        config.ANNOTATOR_MODEL, params=config.LLMParams(max_tokens=config.ANNOTATOR_MAX_TOKENS)
    )
    # Rubric model defaults to the builder; OBCB_RUBRIC_MODEL can point it at a cheaper one.
    rubricator = (
        builder
        if config.RUBRIC_MODEL == config.BUILDER_MODEL
        else LLM(config.RUBRIC_MODEL, params=config.LLMParams(max_tokens=config.BUILDER_MAX_TOKENS))
    )

    profiled = profile_cases(todo, builder)

    items = extract_questions(profiled, builder)
    console.print(f"\nExtracted {len(items)} question(s) with reference solutions")

    items = add_rubrics(items, rubricator)
    console.print(f"{len(items)} question(s) survived the rubric quality gate")

    items = add_metadata(items, annotator)

    profiles = {c["case_name"]: c for c in profiled}
    new_rows = []
    for item in items:
        case = profiles[item["case_name"]]
        new_rows.append(
            {
                "case_name": case["case_name"],
                "case_title": case["case_title"],
                "case_summary": case["case_summary"],
                "case_learning_objectives": case["case_learning_objectives"],
                "fictional_case": case["case_fictional_case"],
                **item,
            }
        )

    # Merge: profiled cases replace/extend cases.jsonl; benchmark rows for any rebuilt case
    # are dropped and replaced so a --force rebuild does not leave stale questions behind.
    for c in profiled:
        existing_cases[c["case_name"]] = c
    rebuilt = {c["case_name"] for c in profiled}
    merged_rows = [r for r in existing_rows if r["case_name"] not in rebuilt] + new_rows

    write_jsonl(cases_out, list(existing_cases.values()))
    write_jsonl(benchmark_out, merged_rows)
    console.print(
        f"\nWrote {len(new_rows)} new benchmark question(s); {len(merged_rows)} total "
        f"-> [bold]{benchmark_out}[/]\n"
        f"Wrote {len(profiled)} newly profiled case(s); {len(existing_cases)} total "
        f"-> [bold]{cases_out}[/]"
    )
    return merged_rows
