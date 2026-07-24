"""Every setting the pipeline has, in one place.

Paths, models, sampling and token budgets, benchmark-construction quality gates,
extraction, bootstrap parameters, and execution limits. Each is overridable by an
``OBCB_*`` environment variable; ``.env`` at the repo root carries only the API key.

Anything here that can change a result is treated as part of the result: ``resolved()``
returns the lot as plain data, and ``save_run_config()`` writes it beside the outputs so a
set of numbers can always be traced to the configuration that produced it.

Paths resolve relative to the repository root, so the CLI works from any cwd.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# .../business_benchmark/src/obcb/config.py -> .../business_benchmark
REPO_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(REPO_ROOT / ".env")


def _path(env: str, default: Path) -> Path:
    raw = os.environ.get(env)
    return Path(raw).expanduser().resolve() if raw else default


DATA_DIR = _path("OBCB_DATA_DIR", REPO_ROOT / "data")
PDF_DIR = _path("OBCB_PDF_DIR", DATA_DIR / "jbca_pairs")
OUT_DIR = _path("OBCB_OUT_DIR", DATA_DIR / "outputs")
ONET_PATH = _path("OBCB_ONET_PATH", DATA_DIR / "onet" / "work_activities.json")

CASES_RAW = OUT_DIR / "cases_raw.jsonl"
CASES = OUT_DIR / "cases.jsonl"
BENCHMARK = OUT_DIR / "benchmark.jsonl"
RESULTS_DIR = OUT_DIR / "results"
CACHE_PATH = OUT_DIR / ".cache" / "llm_cache.jsonl"
REPORT_MD = OUT_DIR / "report.md"
REPORT_HTML = OUT_DIR / "report.html"
USAGE_LOG = OUT_DIR / "usage.jsonl"
SCORES_JSON = OUT_DIR / "scores.json"

BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")


def api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit(
            "OPENROUTER_API_KEY is not set.\n"
            f"  cp {REPO_ROOT / '.env.example'} {REPO_ROOT / '.env'}\n"
            "then fill in your OpenRouter key."
        )
    return key


# Construction models. Kept smaller/cheaper than the solvers, as in the paper
# (gemini-2.5-pro / gemini-2.5-flash for extraction and metadata annotation).
BUILDER_MODEL = os.environ.get("OBCB_BUILDER_MODEL", "google/gemini-2.5-pro")
ANNOTATOR_MODEL = os.environ.get("OBCB_ANNOTATOR_MODEL", "google/gemini-2.5-flash")
# Rubric synthesis. Defaults to the builder; point it at a cheaper model to cut the
# per-question rubric cost (turning a solution into a checklist is largely mechanical).
RUBRIC_MODEL = os.environ.get("OBCB_RUBRIC_MODEL", BUILDER_MODEL)
# The paper holds the judge fixed across all solvers at gemini-2.5-flash.
JUDGE_MODEL = os.environ.get("OBCB_JUDGE_MODEL", "google/gemini-2.5-flash")

# Stage labels for the two evaluation phases. Cost reporting classifies spend by stage:
# these two are the per-(case, model) evaluation cost, everything else is construction.
SOLVE_STAGE = "solving"
GRADE_STAGE = "grading"

DEFAULT_SOLVERS = [
    m.strip()
    for m in os.environ.get(
        "OBCB_SOLVERS",
        "anthropic/claude-sonnet-4.6,openai/gpt-5.4,google/gemini-3-flash-preview",
    ).split(",")
    if m.strip()
]

# --------------------------------------------------------------------------------------
# Sampling and token budgets
#
# These change results: a truncated answer scores lower, so a run is only comparable to
# another run with the same budgets. They are recorded in the run config for that reason.
# Temperature 0 / top_p 1 mirror the reference evaluate_models.py.
# --------------------------------------------------------------------------------------

TEMPERATURE = float(os.environ.get("OBCB_TEMPERATURE", "0.0"))
TOP_P = float(os.environ.get("OBCB_TOP_P", "1.0"))
BUILDER_MAX_TOKENS = int(os.environ.get("OBCB_BUILDER_MAX_TOKENS", "32000"))
ANNOTATOR_MAX_TOKENS = int(os.environ.get("OBCB_ANNOTATOR_MAX_TOKENS", "4000"))
SOLVER_MAX_TOKENS = int(os.environ.get("OBCB_SOLVER_MAX_TOKENS", "10000"))
JUDGE_MAX_TOKENS = int(os.environ.get("OBCB_JUDGE_MAX_TOKENS", "10000"))

# --------------------------------------------------------------------------------------
# Benchmark construction quality gates
#
# These decide which questions exist at all, so two benchmarks built with different gates
# are different benchmarks even from identical source PDFs.
# --------------------------------------------------------------------------------------

MIN_QUESTION_CHARS = int(os.environ.get("OBCB_MIN_QUESTION_CHARS", "20"))
MIN_SOLUTION_CHARS = int(os.environ.get("OBCB_MIN_SOLUTION_CHARS", "20"))
MIN_RUBRIC_CRITERIA = int(os.environ.get("OBCB_MIN_RUBRIC_CRITERIA", "2"))

# --------------------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------------------

# PDF -> markdown extractor. "auto" picks the best local one that is installed:
# pymupdf4llm -> docling -> pypdf. See extractors.py for the full list.
EXTRACTOR = os.environ.get("OBCB_EXTRACTOR", "auto")


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Retain pymupdf4llm's duplicated image-overlay text (see extractors.py).
KEEP_PICTURE_TEXT = _bool_env("OBCB_KEEP_PICTURE_TEXT", False)
# Let docling run its OCR pass. Slower; only needed for scanned pages.
DOCLING_OCR = _bool_env("OBCB_DOCLING_OCR", False)

# --------------------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------------------

# Nonparametric bootstrap over questions, percentile 95% CI. The seed is fixed so
# confidence intervals are reproducible run to run.
BOOTSTRAP_B = int(os.environ.get("OBCB_BOOTSTRAP_B", "500"))
BOOTSTRAP_SEED = int(os.environ.get("OBCB_BOOTSTRAP_SEED", "20260723"))

# --------------------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------------------

CONCURRENCY = int(os.environ.get("OBCB_CONCURRENCY", "8"))
REQUEST_TIMEOUT = float(os.environ.get("OBCB_REQUEST_TIMEOUT", "600"))

# The paper reports 18 disciplines but only enumerates the 16 with n >= 5 (Fig. 2B).
# Questions that fit none of these are labelled "Other".
DISCIPLINES = [
    "Accounting",
    "Business & Government Relations",
    "Business Ethics",
    "Decision Analysis",
    "Economics",
    "Entrepreneurship & Innovation",
    "Finance",
    "General Management",
    "Human Resource Management",
    "Information Technology",
    "International Business",
    "Leadership & Organizational Behavior",
    "Marketing & Sales",
    "Negotiation",
    "Operations & Service Management",
    "Strategy",
    "Other",
]


@dataclass
class LLMParams:
    """Sampling settings for one call site."""

    temperature: float = TEMPERATURE
    top_p: float = TOP_P
    max_tokens: int = SOLVER_MAX_TOKENS
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------------------
# Run manifest
# --------------------------------------------------------------------------------------


def _prompt_digests() -> dict:
    from . import prompts  # local import: prompts must not depend on config

    return prompts.digests()


def resolved() -> dict:
    """Every setting that can change a result, as plain data.

    Written to the run config and embedded in scores.json so a set of numbers can
    always be traced back to the configuration that produced it.
    """
    return {
        "models": {
            "builder": BUILDER_MODEL,
            "annotator": ANNOTATOR_MODEL,
            "rubric": RUBRIC_MODEL,
            "judge": JUDGE_MODEL,
            "solvers": DEFAULT_SOLVERS,
        },
        "sampling": {
            "temperature": TEMPERATURE,
            "top_p": TOP_P,
            "builder_max_tokens": BUILDER_MAX_TOKENS,
            "annotator_max_tokens": ANNOTATOR_MAX_TOKENS,
            "solver_max_tokens": SOLVER_MAX_TOKENS,
            "judge_max_tokens": JUDGE_MAX_TOKENS,
        },
        "quality_gates": {
            "min_question_chars": MIN_QUESTION_CHARS,
            "min_solution_chars": MIN_SOLUTION_CHARS,
            "min_rubric_criteria": MIN_RUBRIC_CRITERIA,
        },
        "extraction": {
            "extractor": EXTRACTOR,
            "keep_picture_text": KEEP_PICTURE_TEXT,
            "docling_ocr": DOCLING_OCR,
        },
        "reporting": {
            "bootstrap_b": BOOTSTRAP_B,
            "bootstrap_seed": BOOTSTRAP_SEED,
        },
        # Prompts change results at least as much as token budgets do.
        "prompts": _prompt_digests(),
        "execution": {
            "concurrency": CONCURRENCY,
            "request_timeout": REQUEST_TIMEOUT,
            "base_url": BASE_URL,
        },
        "disciplines": DISCIPLINES,
    }


def save_run_config(command: str, extra: dict | None = None) -> Path:
    """Write the resolved configuration for this run to OUT_DIR/run_config.json."""
    import json
    from datetime import datetime, timezone

    from . import __version__

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "command": command,
        "obcb_version": __version__,
        "config": resolved(),
        **(extra or {}),
    }
    path = OUT_DIR / "run_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
