"""Prompt templates, loaded from the ``.md`` files beside this module.

Prompts live as markdown so they can be read, reviewed, and edited without opening
Python. That matters here: whether ``extract_questions.md`` faithfully recovers an
instructor's question/answer pairs, and whether ``rubric.md`` turns a reference solution
into fair criteria, are pedagogy judgments, not programming ones.

Two safeguards come with that move:

*Placeholder validation.* In Python a ``{case_text}`` typo sat next to its ``.format()``
call. In a file it would surface as a ``KeyError`` deep inside a stage, after earlier
calls were already paid for. Each prompt declares its placeholders in frontmatter, and
loading fails immediately if the body and the declaration disagree in either direction.

*Hashing.* Prompts change results at least as much as token budgets do. Each one is
hashed into the run config so a set of numbers can be traced to the exact text that
produced it.

``solve.md`` and ``grade.md`` are copied verbatim from the reference implementation.
Editing them breaks comparability with the paper; ``obcb prompts`` flags it when they
have changed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

PROMPT_DIR = Path(__file__).parent

# Prompts whose final string keeps the leading/trailing newline of the reference's
# triple-quoted literal, so the bytes sent to the model match it exactly.
_EDGE_NEWLINES = {"SOLVE_PROMPT"}

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


class PromptError(RuntimeError):
    """Raised at import time so a malformed prompt never reaches a paid API call."""


@dataclass(frozen=True)
class Prompt:
    name: str
    path: Path
    text: str
    description: str
    placeholders: frozenset[str]
    verbatim_source: str | None

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:12]


def _parse_frontmatter(raw: str, path: Path) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER.match(raw)
    if not match:
        raise PromptError(f"{path.name}: missing '---' frontmatter block at the top of the file")
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise PromptError(f"{path.name}: frontmatter line is not 'key: value': {line!r}")
        meta[key.strip()] = value.strip()
    return meta, raw[match.end() :]


def _load_one(path: Path) -> Prompt:
    name = path.stem.upper() + "_PROMPT"
    meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"), path)

    text = body.strip()
    if not text:
        raise PromptError(f"{path.name}: prompt body is empty")
    if name in _EDGE_NEWLINES:
        text = f"\n{text}\n"

    declared = frozenset(
        f.strip() for f in meta.get("placeholders", "").split(",") if f.strip()
    )
    found = frozenset(_PLACEHOLDER.findall(text))
    if declared != found:
        missing = sorted(declared - found)
        undeclared = sorted(found - declared)
        detail = []
        if undeclared:
            detail.append(f"used in the body but not declared: {undeclared}")
        if missing:
            detail.append(f"declared but not used: {missing}")
        raise PromptError(f"{path.name}: placeholder mismatch - {'; '.join(detail)}")

    return Prompt(
        name=name,
        path=path,
        text=text,
        description=meta.get("description", ""),
        placeholders=found,
        verbatim_source=meta.get("source") if meta.get("verbatim") == "true" else None,
    )


def _load_all() -> dict[str, Prompt]:
    files = sorted(PROMPT_DIR.glob("*.md"))
    if not files:
        raise PromptError(f"no prompt files found in {PROMPT_DIR}")
    return {p.name: p for p in (_load_one(f) for f in files)}


PROMPTS: dict[str, Prompt] = _load_all()

# Expose each prompt as a module-level string so call sites stay `prompts.SOLVE_PROMPT`.
globals().update({name: prompt.text for name, prompt in PROMPTS.items()})

# Baselines for the two prompts copied verbatim from the reference implementation.
# A mismatch means evaluation is no longer comparable to the paper.
#
# These were checked against the reference SOURCE FILE, not against an earlier copy of
# this package - an earlier version of grade.md had silently lost a trailing space,
# and a self-comparison could never have caught it. tests/prompts_check.py re-derives
# them from reference-paper-code when it is present.
REFERENCE_DIGESTS = {
    "SOLVE_PROMPT": "1969b4135d03",
    "GRADE_PROMPT": "ebc3a091a523",
}


def digests() -> dict[str, str]:
    """Short hash per prompt, for the run config."""
    return {name: prompt.digest for name, prompt in sorted(PROMPTS.items())}


def modified_from_reference() -> list[str]:
    """Verbatim-from-reference prompts whose text no longer matches the baseline."""
    return [
        name
        for name, expected in REFERENCE_DIGESTS.items()
        if name in PROMPTS and PROMPTS[name].digest != expected
    ]


# Static names for editors and linters; the real values are installed above.
CASE_PROFILE_PROMPT: str
EXTRACT_QUESTIONS_PROMPT: str
RUBRIC_PROMPT: str
METADATA_PROMPT: str
DWA_PROMPT: str
SOLVE_PROMPT: str
GRADE_PROMPT: str
