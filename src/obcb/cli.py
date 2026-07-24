"""Command line entry point. Bare `obcb` runs the pipeline; the rest are subcommands."""

from __future__ import annotations

from pathlib import Path

import click

from . import config


# --------------------------------------------------------------------------------------
# How many cases to process
#
# The default is one: the single most recent case in the list. Running the whole corpus
# through the LLM stages costs real money, so the safe default is the smallest thing that
# exercises the pipeline end to end. -3 / -5 / -10 / -all are shorthands for the common
# choices; --limit N covers everything else.
# --------------------------------------------------------------------------------------

DEFAULT_COUNT = "1"


def count_options(func):
    """Attach -1 / -3 / -5 / -10 / -all / --limit N to a command."""
    for flag in ("-10", "-5", "-3", "-1"):
        func = click.option(
            flag, "count", flag_value=flag.lstrip("-"), help=f"Process {flag.lstrip('-')} case(s)."
        )(func)
    func = click.option(
        "-all", "--all", "count", flag_value="all", help="Process every case in the list."
    )(func)
    func = click.option(
        "--limit", "-n", "limit", type=int, default=None, metavar="N",
        help="Process N cases (overrides the shorthand flags).",
    )(func)
    return func


def resolve_count(count: str | None, limit: int | None) -> int | None:
    """Return the case count, or None for 'all'. --limit wins over the shorthands."""
    if limit is not None:
        if limit < 1:
            raise SystemExit("--limit must be 1 or more; use --all to process every case.")
        return limit
    if (count or DEFAULT_COUNT) == "all":
        return None
    return int(count or DEFAULT_COUNT)


def _run_pipeline(
    n: int | None, models: tuple[str, ...], extractor: str | None, force: bool = False
) -> None:
    """Fetch the first N cases from the list and run extract -> build -> evaluate -> report.

    N is None for the whole list. Every stage is incremental: fetch skips already-downloaded
    PDFs, and extract / build / evaluate skip any case already in their output files, so a case
    that has been fully processed is not touched again (and not re-billed). ``force=True``
    reprocesses everything from scratch.
    """
    from . import build, evaluate, extract, fetch, report

    slugs = fetch.run(limit=n, force=force)
    targets = set(slugs)
    extract.run(extractor=extractor, only=targets, force=force)
    build.run(only=targets, force=force)
    evaluate.run(models=list(models) or None, only=targets, force=force)
    report.run()


@click.group(invoke_without_command=True)
@click.version_option(package_name="obcb")
@count_options
@click.option("--model", "models", multiple=True, help="Solver model (repeatable).")
@click.option("--extractor", default=None, help="PDF extractor (see `obcb extractors`).")
@click.option(
    "--force", is_flag=True, help="Reprocess cases already done instead of skipping them."
)
@click.pass_context
def cli(
    ctx: click.Context,
    count: str | None,
    limit: int | None,
    models: tuple[str, ...],
    extractor: str | None,
    force: bool,
) -> None:
    """Open Business Case Bench - benchmark AI on business case analysis.

    With no subcommand, fetches and runs cases from the top of the list:

      obcb            # the most recent case
      obcb -3         # the top 3
      obcb -all       # every case in the list

    Everything else is a subcommand (fetch-cases, config, cost, ...).
    """
    if ctx.invoked_subcommand is None:
        _run_pipeline(resolve_count(count, limit), models, extractor, force=force)


# Commands that produce artifacts, and so should stamp the config that produced them.
# "run" is the label for the bare `obcb` full-pipeline invocation.
PIPELINE_COMMANDS = {"run", "extract", "build", "evaluate", "report"}


@cli.result_callback()
def _finalize(result, **_kwargs) -> None:
    """Persist the run config and token/cost accounting after every command."""
    from . import usage

    # Bare `obcb` (no subcommand) is the default full-pipeline run.
    command = click.get_current_context().invoked_subcommand or "run"

    if command in PIPELINE_COMMANDS:
        path = config.save_run_config(command)
        click.echo(f"  config -> {path}")

    if usage.LEDGER.is_empty():
        return
    usage.LEDGER.print_summary()
    usage.LEDGER.save(config.USAGE_LOG, command=command)
    click.echo(f"  logged to {config.USAGE_LOG}")


@cli.command(name="fetch-cases")
@count_options
@click.option("--out", type=click.Path(path_type=Path), default=None, help="Where to write pairs.")
@click.option("--force", is_flag=True, help="Re-download even if the pair already exists.")
def fetch_cases(count: str | None, limit: int | None, out: Path | None, force: bool) -> None:
    """Download cases from the list, newest first, and split each into a case/instructor pair."""
    from . import fetch

    fetch.run(limit=resolve_count(count, limit), out_dir=out, force=force)


@cli.command(name="update-case-list")
@click.option("--scan", type=int, default=None, help="Only scan the newest N manuscripts.")
@click.option(
    "--cache",
    type=click.Path(path_type=Path),
    default=None,
    help="Keep accepted PDFs here, and remember rejects so a rescan skips them.",
)
@click.option("--recheck", is_flag=True, help="Re-download manuscripts a previous scan rejected.")
@click.option("--verbose", is_flag=True, help="Say why each rejected manuscript was rejected.")
def update_case_list(scan: int | None, cache: Path | None, recheck: bool, verbose: bool) -> None:
    """Rescan the journal and rewrite data/case_list.json, newest volume first."""
    from . import discover

    discover.build_case_list(scan=scan, cache_dir=cache, recheck=recheck, verbose=verbose)


@cli.command(name="find-cases")
@click.option("--scan", type=int, default=25, show_default=True, help="Manuscripts to inspect.")
@click.option("--limit", type=int, default=5, show_default=True, help="Candidates to keep.")
@click.option("--out", type=click.Path(path_type=Path), default=None, help="Write candidates here.")
@click.option("--verbose", is_flag=True, help="Say why each rejected manuscript was rejected.")
def find_cases(scan: int, limit: int, out: Path | None, verbose: bool) -> None:
    """Scan the source journal for new cases worth adding to the case list."""
    from . import discover

    discover.run(scan=scan, limit=limit, out_path=out, verbose=verbose)


@cli.command()
@click.option("--pdf-dir", type=click.Path(path_type=Path), default=None, help="Paired PDFs.")
@click.option("--out", type=click.Path(path_type=Path), default=None, help="cases_raw.jsonl.")
@click.option(
    "--extractor",
    default=None,
    help="PDF extractor: auto | pypdf | pymupdf4llm | docling | llamaparse | landingai.",
)
@click.option("--force", is_flag=True, help="Re-extract cases already in cases_raw.jsonl.")
def extract(pdf_dir: Path | None, out: Path | None, extractor: str | None, force: bool) -> None:
    """Read case/_instructor PDF pairs into cases_raw.jsonl."""
    from . import extract as stage

    stage.run(pdf_dir=pdf_dir, out_path=out, extractor=extractor, force=force)


@cli.command(name="extractors")
def list_extractors() -> None:
    """List the PDF extractors and whether each is installed and ready."""
    import os

    from rich.console import Console

    from . import extractors

    console = Console()
    chosen = extractors.resolve(config.EXTRACTOR)
    for name, b in extractors.EXTRACTORS.items():
        if b.env and not os.environ.get(b.env):
            status = f"[yellow]needs {b.env}[/]"
        elif extractors.available(name):
            status = "[green]ready[/]"
        else:
            status = "[dim]not installed[/]"
        caps = ", ".join(
            filter(None, [b.kind, "tables" if b.tables else None, "ocr" if b.ocr else None])
        )
        selected = " [bold cyan]<- selected[/]" if name == chosen else ""
        console.print(f"\n  [bold]{name}[/] ({caps})  {status}{selected}")
        console.print(f"    {b.note}")
        console.print(f"    [dim]{b.install}[/]")


@cli.command()
@click.option("--limit", type=int, default=None, help="Only build from the first N cases.")
@click.option("--force", is_flag=True, help="Rebuild cases already in cases.jsonl.")
def build(limit: int | None, force: bool) -> None:
    """Extract questions, reference solutions, rubrics, and metadata."""
    from . import build as stage

    stage.run(limit=limit, force=force)


@cli.command()
@click.option("--model", "models", multiple=True, help="Solver model (repeatable).")
@click.option("--limit", type=int, default=None, help="Only evaluate the first N questions.")
@click.option("--force", is_flag=True, help="Re-evaluate questions already in the results.")
def evaluate(models: tuple[str, ...], limit: int | None, force: bool) -> None:
    """Run solver models and grade them against the rubrics."""
    from . import evaluate as stage

    stage.run(models=list(models) or None, limit=limit, force=force)


@cli.command()
def report() -> None:
    """Aggregate results into Standard and Complete Answer scores."""
    from . import report as stage

    stage.run()


@cli.command(name="html")
@click.option("--scores", type=click.Path(path_type=Path), default=None, help="scores.json.")
@click.option("--out", type=click.Path(path_type=Path), default=None, help="Output .html.")
def html(scores: Path | None, out: Path | None) -> None:
    """Rebuild the HTML report from scores.json, without re-running anything."""
    from . import html_report

    scores = scores or config.SCORES_JSON
    if not scores.exists():
        raise SystemExit(f"{scores} not found. Run `obcb report` first.")
    path = html_report.write_from_scores(scores, out or config.REPORT_HTML)
    click.echo(f"Wrote {path}")


@cli.command(name="prompts")
@click.option("--show", "show", default=None, metavar="NAME", help="Print one prompt in full.")
def show_prompts(show: str | None) -> None:
    """List the prompt templates, or print one. Edit them as markdown in src/obcb/prompts/."""
    from rich.console import Console

    from . import prompts as prompt_mod

    console = Console()

    if show:
        key = show.upper()
        key = key if key.endswith("_PROMPT") else f"{key}_PROMPT"
        prompt = prompt_mod.PROMPTS.get(key)
        if prompt is None:
            raise SystemExit(
                f"unknown prompt '{show}'. Available: "
                + ", ".join(sorted(k.replace("_PROMPT", "").lower() for k in prompt_mod.PROMPTS))
            )
        console.print(f"[dim]{prompt.path}[/]\n")
        click.echo(prompt.text)
        return

    for name, prompt in sorted(prompt_mod.PROMPTS.items()):
        tag = " [yellow](verbatim from reference)[/]" if prompt.verbatim_source else ""
        console.print(f"\n  [bold]{prompt.path.name}[/]  {prompt.digest}{tag}")
        console.print(f"    {prompt.description}")
        console.print(f"    [dim]placeholders: {', '.join(sorted(prompt.placeholders))}[/]")

    drifted = prompt_mod.modified_from_reference()
    if drifted:
        console.print(
            "\n  [yellow]WARNING:[/] "
            + ", ".join(drifted)
            + " no longer match the reference implementation.\n"
            "  Evaluation results are no longer comparable to the paper."
        )
    else:
        console.print("\n  [green]solve and grade match the reference implementation.[/]")


@cli.command()
@click.option("--limit", type=int, default=20, help="Show the last N runs.")
def cost(limit: int) -> None:
    """Show token usage and spend across previous runs."""
    import json

    from rich.console import Console
    from rich.table import Table

    console = Console()
    if not config.USAGE_LOG.exists():
        raise SystemExit(f"No usage log yet at {config.USAGE_LOG}. Run a stage first.")

    runs = [json.loads(x) for x in config.USAGE_LOG.read_text(encoding="utf-8").splitlines() if x]

    table = Table(title=f"Run history ({len(runs)} run(s))")
    for col in ("When (UTC)", "Command", "Calls", "Cached", "Prompt tok", "Output tok", "Cost USD"):
        table.add_column(col, justify="right" if col not in ("When (UTC)", "Command") else "left")
    for run in runs[-limit:]:
        t = run["total"]
        table.add_row(
            run["timestamp"].replace("+00:00", ""),
            run["command"],
            str(t["calls"]),
            str(t["cached_calls"]) or "-",
            f"{t['prompt_tokens']:,}",
            f"{t['completion_tokens']:,}",
            f"${t['cost_usd']:.4f}",
        )
    console.print(table)

    grand = sum(r["total"]["cost_usd"] for r in runs)
    tokens = sum(r["total"]["prompt_tokens"] + r["total"]["completion_tokens"] for r in runs)
    saved = sum(r["total"]["saved_usd"] for r in runs)
    console.print(
        f"\n  [bold]${grand:.4f}[/] total across all runs | {tokens:,} tokens"
        f" | [green]${saved:.4f} saved by cache[/]"
    )

    by_model: dict[str, float] = {}
    for run in runs:
        for model, stat in run.get("by_model", {}).items():
            by_model[model] = by_model.get(model, 0.0) + stat["cost_usd"]
    if by_model:
        console.print("\n  By model:")
        for model, spend in sorted(by_model.items(), key=lambda kv: -kv[1]):
            console.print(f"    {model:<40} ${spend:.4f}")


@cli.command(name="config")
@click.option("--json", "as_json", is_flag=True, help="Emit the full resolved config as JSON.")
def show_config(as_json: bool) -> None:
    """Show every resolved setting. This is exactly what a run records."""
    import json as jsonlib

    if as_json:
        click.echo(jsonlib.dumps(config.resolved(), indent=2))
        return

    cfg = config.resolved()
    sections = [
        ("paths", {
            "repo root": config.REPO_ROOT,
            "pdf dir": config.PDF_DIR,
            "output dir": config.OUT_DIR,
            "O*NET data": config.ONET_PATH,
            "cache": config.CACHE_PATH,
        }),
        ("models", {**cfg["models"], "solvers": ", ".join(cfg["models"]["solvers"])}),
        ("sampling", cfg["sampling"]),
        ("quality gates", cfg["quality_gates"]),
        ("extraction", cfg["extraction"]),
        ("reporting", cfg["reporting"]),
        ("execution", cfg["execution"]),
    ]
    for name, values in sections:
        click.echo(f"\n[{name}]")
        for label, value in values.items():
            click.echo(f"  {label.replace('_', ' '):>22}: {value}")


if __name__ == "__main__":
    cli()
