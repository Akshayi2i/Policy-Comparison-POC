"""Narrative containers and their deterministic defaults (used offline and as fallback for model output)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from policy_compare.findings import IMPACT_RANK, SEVERITY_RANK, Finding
from policy_compare.fmt import money, plural
from policy_compare.settings import config

SECTION_TITLES = {
    "policy": "Policy Information", "premium": "Premium and Financial Terms", "limits": "Limits and Sub-limits",
    "terms": "Coverage Terms and Conditions", "forms": "Endorsements and Forms",
    "midterm": "Mid-term Changes on the Expiring Policy", "checklist": "Contract Requirements and Broker Checklist",
    "focus": "Client Focus Areas", "pending": "Items Pending Confirmation with the Carrier",
    "observations": "Coverage Observations (No Change at Renewal)",
}
SECTION_ORDER = ["policy", "premium", "limits", "terms", "forms", "midterm", "checklist", "focus", "pending", "observations"]
# sections whose rows describe changes (observations describe existing terms, so they never count as changes)
CHANGE_SECTIONS = SECTION_ORDER[:-1]
PRIMARY = {"policy", "premium", "limits", "terms", "forms", "midterm", "checklist"}
LEVELS = ["low", "medium", "high"]


@dataclass
class SectionNarrative:
    headline: str
    bullets: list[str]
    takeaway: str
    risk_level: str
    risk_statement: str
    confidence: str
    confidence_reason: str
    source: str = "default"


@dataclass
class CriticalNarrative:
    title: str
    theme: str
    description: str
    next_step: str
    rank: int = 0
    source: str = "default"


@dataclass
class ExecNarrative:
    risk: str
    confidence: str
    lead: str
    body: str
    confidence_note: str
    source: str = "default"


@dataclass
class Narratives:
    sections: dict[str, SectionNarrative] = field(default_factory=dict)
    critical: dict[str, CriticalNarrative] = field(default_factory=dict)   # by finding id
    critical_order: list[str] = field(default_factory=list)
    executive: Optional[ExecNarrative] = None
    focus_quotes: dict[str, list[tuple[str, int]]] = field(default_factory=dict)  # topic -> [(side, sentence idx)]


# ---------------- risk ----------------

def floor_risk(rows: list[Finding]) -> str:
    """Rubric floor for a section's risk level."""
    rub = config("rubric")["risk"]
    hi = rub["floor_high_if"]
    if any(r.impact == hi["impact"] and r.severity == hi["severity"] for r in rows):
        return "high"
    if any(r.impact == c["impact"] and r.severity == c["severity"] for c in rub["floor_medium_if"] for r in rows):
        return "medium"
    if any(r.impact == "reduced" for r in rows):
        return "medium"
    return "low"


def max_level(*levels: str) -> str:
    return max(levels, key=LEVELS.index)


def min_level(*levels: str) -> str:
    return min(levels, key=LEVELS.index)


# ---------------- defaults ----------------

def _sentence(s: str) -> str:
    s = s.strip()
    return s if s.endswith((".", "!", "?")) else s + "."


def _short(label: str, n: int = 48) -> str:
    label = re.sub(r"\s+—\s+.*$", "", label) if len(label) > n else label
    return label if len(label) <= n else label[: n - 1] + "…"


def default_section(section: str, rows: list[Finding], ctx: dict) -> SectionNarrative:
    changed = [r for r in rows if r.changed]
    material = [r for r in changed if r.impact != "no_impact"]
    ranked = sorted(material, key=lambda r: (IMPACT_RANK.get(r.impact, 9), SEVERITY_RANK.get(r.severity, 9)))
    level = floor_risk(rows)
    name = SECTION_TITLES[section].lower()

    if section == "focus" and not ctx.get("topics"):
        headline = "No client focus topics were supplied for this comparison."
    elif section == "focus":
        hit = [r for r in rows if r.changed]
        n = len(ctx["topics"])
        if not hit:
            headline = f"None of the {plural(n, 'client focus topic')} is affected by the renewal."
        else:
            headline = f"{len(hit)} of {plural(n, 'focus topic')} {'is' if len(hit) == 1 else 'are'} affected by the renewal: " + \
                       ", ".join(r.label for r in hit) + "."
    elif section == "midterm" and not rows:
        headline = "No mid-term changes were found on the expiring policy."
    elif section == "pending" and not rows:
        headline = "Nothing is waiting on the carrier."
    elif section == "observations" and not rows:
        headline = ("No coverage observations were raised for this client." if ctx.get("observations_ran")
                    else "Coverage observations need the model; none were produced in this run.")
    elif section == "observations":
        headline = _sentence("Unchanged terms to discuss with the client: " +
                             "; ".join(r.label[:1].lower() + r.label[1:] for r in rows[:3]))
    elif not changed:
        headline = f"No changes found in {name}."
    elif not material:
        headline = "Only administrative changes: " + ", ".join(_short(r.label).lower() for r in changed[:3]) + "."
    else:
        n_red = sum(r.impact == "reduced" for r in material)
        n_conf = sum(r.impact == "confirm" for r in material)
        n_imp = sum(r.impact == "improved" for r in material)
        parts = []
        if n_red:
            parts.append(f"{plural(n_red, 'change')} reduce{'s' if n_red == 1 else ''} cover")
        if n_conf:
            parts.append(f"{plural(n_conf, 'item')} need{'s' if n_conf == 1 else ''} confirmation")
        if n_imp:
            parts.append(f"{n_imp} improve{'s' if n_imp == 1 else ''} on the expiring terms")
        headline = _sentence("; ".join(parts)[:1].upper() + "; ".join(parts)[1:])

    bullets = [_sentence(f"{_short(r.label)}: {r.change}") for r in ranked[:3]]
    if not bullets and changed:
        bullets = [_sentence(f"{_short(r.label)}: {r.change}") for r in changed[:2]]
    if section == "focus" and ctx.get("topics"):
        bullets = bullets or [f"{len(ctx['topics'])} topic{'s' if len(ctx['topics']) != 1 else ''} reviewed; no related changes found."]

    if level == "high":
        stmt = f"{plural(sum(r.impact == 'reduced' and r.severity == 'critical' for r in rows), 'critical reduction')} in this section change what is insured."
    elif level == "medium":
        stmt = "Some changes reduce cover or need confirmation before the renewal is presented."
    elif material:
        stmt = "The changes here are minor; confirm them in the normal course."
    else:
        stmt = "Nothing in this section reduces the client's cover."

    if section == "observations":
        stmt = ("These terms did not change at renewal, but they leave gaps worth discussing with the client."
                if rows else "Nothing in this section changes at renewal.")
    conf, reason = default_confidence(section, rows, ctx)
    return SectionNarrative(headline=headline, bullets=bullets, takeaway=headline, risk_level=level, risk_statement=stmt,
                            confidence=conf, confidence_reason=reason)


def default_confidence(section: str, rows: list[Finding], ctx: dict) -> tuple[str, str]:
    pages = ctx.get("decl_pages", "")
    scanned = ctx.get("scanned", 0)
    if section in ("policy", "premium", "limits", "terms"):
        missing_refs = [r for r in rows if r.changed and ((r.exp != "—" and not r.exp_ref) or (r.ren not in ("—", "") and not r.ren_ref))]
        if scanned:
            return "medium", f"{plural(scanned, 'page')} had no text layer; values on those pages could not be read."
        if missing_refs:
            return "medium", f"{plural(len(missing_refs), 'changed value')} could not be located on a page; confirm against the documents."
        return "high", f"All values read from digital declarations and schedules ({pages})." if pages else "All values read from the digital policy text."
    if section == "forms":
        return "high", "Form numbers and editions matched on both forms schedules; attached form text compared word for word."
    if section == "midterm":
        return ("high", "No policy-change documents found in the expiring policy file.") if not rows else \
               ("medium", "Policy-change documents were detected; their effect is read from the endorsement text.")
    if section == "checklist":
        by_title = ctx.get("slots_by_title")
        return ("medium", "Carrier-specific forms were matched to requirements by title; confirm their wording meets the contracts.") if by_title else \
               ("high", "Requirements matched on form numbers listed on both forms schedules.")
    if section == "focus":
        return "high", "Mentions are counted across the full policy text; quotes are verbatim with page references."
    if section == "pending":
        return ("low", "The change is clear; the reason for it is not stated in the documents.") if rows else ("high", "No unexplained removals or additions.")
    return "medium", ""


def default_critical(f: Finding, deadline: str) -> CriticalNarrative:
    rub = config("rubric")
    themes, steps = rub.get("themes", {}), rub.get("next_step_templates", {})
    if f.kind == "continuity":
        title, theme = "Policy period gap", themes.get("continuity", "continuity")
    elif f.kind == "coverage_parts":
        title, theme = f"{', '.join(f.context.get('dropped', [])) or 'Coverage part'} dropped", themes.get("coverage_parts", "scope")
    elif f.kind == "admitted":
        title, theme = "Now non-admitted (surplus lines)", themes.get("admitted", "market")
    elif f.kind.startswith("form_"):
        num = f.context.get("form_number", f.label)
        title = f"{num} replaced by {f.context.get('renewal_form_number')}" if f.kind == "form_replaced" else \
            f"{num} {'removed' if f.kind == 'form_removed' else 'added' if f.kind == 'form_added' else 'changed'}"
        theme = themes.get("form_contract_slot") if f.context.get("contract_slots") else themes.get(f.kind, "scope")
    else:
        verb = "changed"
        if f.impact == "reduced":
            if f.type == "deductible":
                verb = "introduced" if f.exp in ("None", "—") else "increased"
            elif f.ren in ("None", "—", "Not found"):
                verb = "removed"
            else:
                verb = "reduced"
        title = f"{_short(f.label, 34)} {verb}"
        theme = themes.get(f.section, themes.get("default", "scope"))
    key = f.kind if f.kind in steps else (
        "deductible_reduced" if f.type == "deductible" and f.impact == "reduced" else
        "value_reduced" if f.impact == "reduced" else "value_confirm")
    step = steps.get(key, steps.get("default", "")).format(label=f.context.get("form_number") or _short(f.label, 40), deadline=deadline)
    first = (f.why or f.change).split(". ")[0].rstrip(".")
    words = first.split()
    desc = (" ".join(words[:18]) + ("…" if len(words) > 18 else ".")) if words else f.change
    return CriticalNarrative(title=title, theme=theme, description=desc, next_step=step)


def default_exec(sections: dict[str, SectionNarrative], unique: list[Finding], critical: list[Finding], premium: Optional[tuple[float, float]],
                 deadline: str) -> ExecNarrative:
    risk = "low"
    for k, n in sections.items():
        if k in PRIMARY:
            risk = max_level(risk, n.risk_level)
    confs = [n for k, n in sections.items() if k in PRIMARY]
    conf = min_level(*(n.confidence for n in confs)) if confs else "medium"
    weakest = next((n for n in confs if n.confidence == conf), None)
    reduced = [f for f in unique if f.impact == "reduced"]
    confirm = [f for f in unique if f.impact == "confirm"]
    if premium:
        a, b = premium
        prem = "unchanged" if a == b else (f"{money(a - b)} lower" if b < a else f"{money(b - a)} higher")
    else:
        prem = None
    if critical:
        lead = "Do not present this renewal as like-for-like."
        body = (f"Premium is {prem}, but " if prem else "") + ("key changes include " if prem else "Key changes include ") + \
            "; ".join(_short(f.label, 40) + f" ({f.change[:1].lower() + f.change[1:]})" for f in critical[:3]) + "."
    elif reduced:
        lead = "Review before presenting."
        body = (f"Premium is {prem}. " if prem else "") + \
            f"{plural(len(reduced), 'change')} reduce{'s' if len(reduced) == 1 else ''} cover" + \
            (f" and {plural(len(confirm), 'item')} need{'s' if len(confirm) == 1 else ''} confirmation" if confirm else "") + \
            f" before the {deadline} expiry."
    elif confirm:
        lead = "Largely like-for-like renewal."
        body = (f"Premium is {prem} and " if prem else "") + "no change reduces cover; " + \
            f"{plural(len(confirm), 'item')} need{'s' if len(confirm) == 1 else ''} confirmation with the carrier before the {deadline} expiry."
    else:
        lead = "Like-for-like renewal."
        body = (f"Premium is {prem}; " if prem else "") + "no coverage reductions were found between the expiring and renewal documents."
    note = f"Confidence is {conf.capitalize()} because " + (weakest.confidence_reason[0].lower() + weakest.confidence_reason[1:] if weakest and weakest.confidence_reason else "the documents were read digitally.")
    return ExecNarrative(risk=risk, confidence=conf, lead=lead, body=body, confidence_note=note)
