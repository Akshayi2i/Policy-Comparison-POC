"""Finding: one compared item, the unit everything downstream (model, guards, report) works on."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

SECTION_NUMBERS = {
    "policy": "3", "premium": "4", "limits": "5", "terms": "6", "forms": "7",
    "midterm": "8", "checklist": "9", "focus": "10", "pending": "11",
}
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
IMPACT_RANK = {"reduced": 0, "confirm": 1, "no_impact": 2, "improved": 3}


@dataclass
class Finding:
    id: str
    change_key: str                 # identity of the underlying change (shared by cross-reference rows)
    section: str                    # policy | premium | limits | terms | forms | midterm | checklist | pending
    kind: str                       # value | continuity | coverage_parts | admitted | form_removed | form_added | form_edition | form_wording | ...
    label: str
    group: str = ""
    exp: str = "—"                  # display values (plain text; chips added from *_ref)
    ren: str = "—"
    exp_ref: Optional[str] = None   # "E1 p.4"
    ren_ref: Optional[str] = None
    change: str = "No change"
    impact: Optional[str] = None    # None = no change (muted row, not counted)
    severity: Optional[str] = None
    locked: bool = False            # impact/severity fixed by the rubric; the model may only explain
    why: Optional[str] = None
    why_source: str = "template"    # template | model
    explain_label: str = "Why it matters"
    tag: Optional[str] = None
    sublabel: Optional[str] = None
    always_show: bool = False
    element_key: Optional[str] = None
    type: str = "text"
    context: dict = field(default_factory=dict)   # facts for the model (amounts, %, excerpts)
    duplicate_of: Optional[str] = None             # change_key this row repeats (not counted twice)

    @property
    def changed(self) -> bool:
        return self.impact is not None

    @property
    def number(self) -> str:
        return SECTION_NUMBERS[self.section]

    def facts(self) -> dict:
        """Compact, value-bearing view handed to the model (it never returns these values)."""
        d = {"id": self.id, "label": self.label, "group": self.group, "expiring": self.exp, "renewal": self.ren,
             "change": self.change, "kind": self.kind, "type": self.type}
        if self.impact:
            d["impact"], d["severity"] = self.impact, self.severity
            d["fixed_by_rules"] = self.locked
        d.update({k: v for k, v in self.context.items() if v not in (None, "", [], {})})
        return d


def sort_key(f: Finding):
    return (IMPACT_RANK.get(f.impact or "", 9), SEVERITY_RANK.get(f.severity or "", 9))
