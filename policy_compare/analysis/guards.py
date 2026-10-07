"""Guards applied after the model: facts stay facts; risk stays between the rubric floor and what the changes support."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from policy_compare.analysis.narrative import PRIMARY, SECTION_ORDER, floor_risk, max_level, min_level

if TYPE_CHECKING:
    from policy_compare.engine import Analysis

_NUM = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?|\d{1,2}/\d{1,2}/\d{2,4}")


def allowed_numbers(an: "Analysis") -> set[str]:
    """Every number/date the narrative may mention: anything shown in a finding, KPI-derived values, counts."""
    vals: set[str] = set()
    for f in an.all_findings():
        for s in (f.exp, f.ren, f.change, f.label, f.why or ""):
            vals.update(_NUM.findall(s or ""))
        for v in f.context.values():
            if isinstance(v, (int, float)):
                vals.add(f"{v:,.0f}".replace(",", ""))
    vals.update(str(n) for n in range(0, 61))
    vals.add(an.deadline)
    return {re.sub(r"[,$%]", "", v) for v in vals}


def unsupported_numbers(text: str, allowed: set[str]) -> list[str]:
    bad = []
    for m in _NUM.findall(text or ""):
        core = re.sub(r"[,$%]", "", m)
        if core not in allowed and not any(core in a for a in allowed):
            bad.append(m)
    return bad


def ceiling_risk(rows) -> str:
    """Highest risk the facts can support: administrative-only changes are low; without any reduction or
    high-severity confirmation a section cannot be high."""
    material = [r for r in rows if r.changed and r.impact != "no_impact"]
    if not material:
        return "low"
    if any(r.impact == "reduced" for r in material) or any(r.impact == "confirm" and r.severity in ("critical", "high") for r in material):
        return "high"
    return "medium"


def apply_guards(an: "Analysis") -> None:
    log = an.audit.setdefault("guards", [])
    # 1. section risk: rubric floor, and a ceiling so the model cannot alarm on administrative changes
    for s in SECTION_ORDER:
        n = an.narratives.sections.get(s)
        if not n:
            continue
        rows = an.findings.get(s, [])
        floor = floor_risk(rows) if s in PRIMARY else "low"
        ceiling = ceiling_risk(rows)
        new = min_level(max_level(n.risk_level, floor), ceiling)
        if new != n.risk_level:
            log.append(f"section {s}: risk {n.risk_level} -> {new} (floor {floor}, ceiling {ceiling})")
            n.risk_level = new
    # 2. overall risk: at least the highest primary section, at most what the changes support
    ex = an.narratives.executive
    if ex:
        top = "low"
        for s in PRIMARY:
            if s in an.narratives.sections:
                top = max_level(top, an.narratives.sections[s].risk_level)
        new = min_level(max_level(ex.risk, top), ceiling_risk(an.unique_changes()))
        if new != ex.risk:
            log.append(f"overall risk {ex.risk} -> {new}")
            ex.risk = new
    # 3. critical list only holds real candidates
    valid = {f.id for f in an.critical_candidates()}
    dropped = [i for i in an.narratives.critical_order if i not in valid]
    if dropped:
        log.append(f"critical list: dropped unknown ids {dropped}")
        an.narratives.critical_order = [i for i in an.narratives.critical_order if i in valid]


_MD = re.compile(r"[*_`#>]+")


def clean_text(text, max_words: int, allowed: set[str] | None = None, forbid: list[str] | None = None):
    """Validate one piece of model text. Returns the cleaned text, or None when it must fall back to the default:
    empty, far over length, mentions a number/date not present in the facts, or contains a forbidden phrase."""
    if not text or not isinstance(text, str):
        return None
    t = _MD.sub("", text).strip().strip('"').strip()
    t = re.sub(r"\s+", " ", t)
    if not t:
        return None
    words = t.split()
    if len(words) > max_words * 1.6:
        return None
    if allowed is not None and unsupported_numbers(t, allowed):
        return None
    if forbid and any(p.lower() in t.lower() for p in forbid):
        return None
    return t
