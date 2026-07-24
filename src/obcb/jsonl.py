"""JSONL helpers, including the reference pipeline's "pretty jsonl" dialect."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Read strict one-object-per-line JSONL, falling back to the pretty dialect."""
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        return []
    try:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    except json.JSONDecodeError:
        return read_pretty_jsonl(path)


def read_pretty_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Read indented JSON objects separated by blank lines.

    This is the format the reference pipeline writes via
    ``export_to_json(..., indent=4)`` and reads back in ``utils/data_utils.py``.
    """
    text = Path(path).read_text(encoding="utf-8").strip()
    return [json.loads(block) for block in re.split(r"\n\s*\n", text) if block.strip()]


def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def write_pretty_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> Path:
    """Write the reference pipeline's indented dialect, for drop-in compatibility."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, indent=4) + "\n\n")
    return path
