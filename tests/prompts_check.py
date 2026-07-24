"""Assert the markdown prompt loader is faithful and fails loudly on bad edits.

Moving prompts out of Python trades compile-time visibility for editability. These
checks buy that back: the loaded text must be byte-identical to what the call sites
used before, and every way a hand-edited file can go wrong must raise at load time
rather than as a KeyError mid-run.
"""

from __future__ import annotations

import importlib
import shutil
import sys
import tempfile
from pathlib import Path

from obcb import config
from obcb import prompts as P

ok = True


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok
    ok = ok and cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  ' + detail if detail else ''}")


# --- the seven prompts still exist and are wired to their call sites -------------------

EXPECTED = {
    "CASE_PROFILE_PROMPT": {"case_text", "instructor_text"},
    "EXTRACT_QUESTIONS_PROMPT": {"case_text", "instructor_text"},
    "RUBRIC_PROMPT": {"question", "solution"},
    "METADATA_PROMPT": {"discipline_list", "iwa_list", "question", "solution"},
    "DWA_PROMPT": {"dwa_list", "question"},
    "SOLVE_PROMPT": {"case_clean_text", "question"},
    "GRADE_PROMPT": {"case_summary", "grading_rubric_list", "model_answer", "question", "solution"},
}

check("all seven prompts load", set(P.PROMPTS) == set(EXPECTED), f"{len(P.PROMPTS)} loaded")
check(
    "module-level constants are strings",
    all(isinstance(getattr(P, n, None), str) and getattr(P, n) for n in EXPECTED),
)
check(
    "placeholders match what the call sites pass",
    all(P.PROMPTS[n].placeholders == fields for n, fields in EXPECTED.items()),
)

# Every prompt must actually render with its declared fields - this is what a call site does.
rendered = 0
for name, fields in EXPECTED.items():
    try:
        getattr(P, name).format(**{f: "x" for f in fields})
        rendered += 1
    except (KeyError, IndexError) as exc:  # noqa: PERF203
        check(f"{name} renders", False, str(exc))
check("every prompt renders with its declared fields", rendered == len(EXPECTED))

# --- reference fidelity ---------------------------------------------------------------

check(
    "SOLVE_PROMPT keeps the reference's edge newlines",
    P.SOLVE_PROMPT.startswith("\n") and P.SOLVE_PROMPT.endswith("\n"),
)
check(
    "other prompts are stripped",
    not any(getattr(P, n)[0].isspace() for n in EXPECTED if n != "SOLVE_PROMPT"),
)
check("solve/grade match the recorded reference baseline", P.modified_from_reference() == [])

# Compare against the reference SOURCE, not against a snapshot of this package. A
# self-comparison only proves the copy is self-consistent; it cannot catch a
# transcription error made before the snapshot was taken (one did happen: grade.md
# lost a trailing space, and the snapshot test happily confirmed the loss).
import re  # noqa: E402

REF_FILE = (
    config.REPO_ROOT / "reference-paper-code/pipeline/prompts/evaluate_models_grading.py"
)
if REF_FILE.exists():
    ref_src = REF_FILE.read_text(encoding="utf-8")
    solve_ref = re.search(
        r'EVALUATE_MODEL_ON_QUESTION_PROMPT = """(.*?)"""', ref_src, re.S
    ).group(1)
    grade_ref = re.search(
        r'GRADE_MODEL_ANSWER_AGAINST_RUBRIC_PROMPT = """(.*?)"""\.strip\(\)', ref_src, re.S
    ).group(1).strip()
    check(
        "solve.md is byte-identical to the reference source",
        P.SOLVE_PROMPT == f"\n{solve_ref.strip()}\n",
        f"{len(P.SOLVE_PROMPT)} vs {len(solve_ref.strip()) + 2}",
    )
    check(
        "grade.md is byte-identical to the reference source",
        P.GRADE_PROMPT == grade_ref,
        f"{len(P.GRADE_PROMPT)} vs {len(grade_ref)}",
    )
else:
    print("SKIP  byte-comparison against reference source (reference-paper-code absent)")

# --- digests reach the run config ---------------------------------------------------

digests = P.digests()
check("digests cover every prompt", set(digests) == set(EXPECTED))
check("digests are stable across calls", digests == P.digests())
check("run config carries prompt digests", config.resolved().get("prompts") == digests)

# --- malformed files must fail at load, not at call time ------------------------------


def load_broken(mutate) -> str | None:
    """Copy the prompt dir, corrupt rubric.md, reload, and return the error message."""
    tmp = Path(tempfile.mkdtemp(prefix="obcb-prompts-"))
    pkg = tmp / "obcb_prompts_probe"
    shutil.copytree(P.PROMPT_DIR, pkg)
    (pkg / "__init__.py").write_text(
        (P.PROMPT_DIR / "__init__.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    mutate(pkg / "rubric.md")
    sys.path.insert(0, str(tmp))
    try:
        importlib.import_module("obcb_prompts_probe")
        return None
    except Exception as exc:  # noqa: BLE001 - we are asserting on the failure
        return f"{type(exc).__name__}: {exc}"
    finally:
        sys.path.remove(str(tmp))
        sys.modules.pop("obcb_prompts_probe", None)
        shutil.rmtree(tmp, ignore_errors=True)


def typo_placeholder(path: Path) -> None:
    path.write_text(path.read_text(encoding="utf-8").replace("{question}", "{questoin}"), "utf-8")


def drop_frontmatter(path: Path) -> None:
    body = path.read_text(encoding="utf-8").split("---\n", 2)[-1]
    path.write_text(body, encoding="utf-8")


def empty_body(path: Path) -> None:
    head = path.read_text(encoding="utf-8").split("---\n")[1]
    path.write_text(f"---\n{head}---\n\n", encoding="utf-8")


for label, mutate, needle in [
    ("typo'd placeholder is caught at load", typo_placeholder, "placeholder mismatch"),
    ("missing frontmatter is caught at load", drop_frontmatter, "frontmatter"),
    ("empty body is caught at load", empty_body, "empty"),
]:
    err = load_broken(mutate)
    check(label, err is not None and needle in err, (err or "no error raised")[:90])

print()
sys.exit(0 if ok else 1)
