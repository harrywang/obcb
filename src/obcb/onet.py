"""O*NET work-activity taxonomy lookups.

Mirrors ``reference-paper-code/pipeline/utils/onet_utils.py`` but exposes the parent
links the reference module builds only in one direction, so a chosen IWA id resolves
back to its Work Activity and forward to its Detailed Work Activities.
"""

from __future__ import annotations

import json
from functools import lru_cache

from . import config


class Taxonomy:
    def __init__(self, rows: list[dict]):
        self.work_activities: dict[str, str] = {}
        self.iwas: dict[str, str] = {}
        self.dwas: dict[str, str] = {}
        self.iwa_to_wa: dict[str, str] = {}
        self.dwas_for_iwa: dict[str, list[str]] = {}

        for row in rows:
            self.work_activities.setdefault(row["element_id"], row["element_name"])
            self.iwas.setdefault(row["iwa_id"], row["iwa_title"])
            self.dwas.setdefault(row["dwa_id"], row["dwa_title"])
            self.iwa_to_wa.setdefault(row["iwa_id"], row["element_id"])
            bucket = self.dwas_for_iwa.setdefault(row["iwa_id"], [])
            if row["dwa_id"] not in bucket:
                bucket.append(row["dwa_id"])

    def iwa_list(self) -> str:
        """Numbered IWA menu for prompting (187 entries, ~11k characters)."""
        return "\n".join(f"- {i}: {t}" for i, t in sorted(self.iwas.items()))

    def dwa_list(self, iwa_id: str) -> str:
        return "\n".join(f"- {d}: {self.dwas[d]}" for d in self.dwas_for_iwa.get(iwa_id, []))

    def resolve(self, iwa_id: str | None, dwa_id: str | None = None) -> dict[str, str | None]:
        """Expand a chosen IWA (and optional DWA) into the six benchmark fields."""
        if iwa_id not in self.iwas:
            iwa_id = None
        wa_id = self.iwa_to_wa.get(iwa_id) if iwa_id else None
        if iwa_id and dwa_id not in self.dwas_for_iwa.get(iwa_id, []):
            dwa_id = None
        return {
            "work_activity": self.work_activities.get(wa_id) if wa_id else None,
            "work_activity_id": wa_id,
            "intermediate_work_activity": self.iwas.get(iwa_id) if iwa_id else None,
            "intermediate_work_activity_id": iwa_id,
            "detailed_work_activity": self.dwas.get(dwa_id) if dwa_id else None,
            "detailed_work_activity_id": dwa_id,
        }


@lru_cache(maxsize=1)
def load() -> Taxonomy:
    if not config.ONET_PATH.exists():
        raise SystemExit(
            f"O*NET taxonomy not found at {config.ONET_PATH}. "
            "Copy work_activities.json there or set OBCB_ONET_PATH."
        )
    return Taxonomy(json.loads(config.ONET_PATH.read_text(encoding="utf-8")))
