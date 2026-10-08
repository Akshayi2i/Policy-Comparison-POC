"""Recommended actions for the account manager (Executive Summary).

Every action must rest on findings of the comparison: the model cites them by id, code checks the ids, caps the
priority at what those findings support, runs the text through the gate and adds the page references. A rule-based
list is always built first; it is used when the model is unavailable, and it keeps every critical change covered.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from policy_compare.analysis.guards import TextGate
from policy_compare.analysis.narrative import SECTION_TITLES
from policy_compare.findings import SEVERITY_RANK, Finding
from policy_compare.llm.client import LLMClient, LLMError, prompt
from policy_compare.schema.llm_io import Recommendations

if TYPE_CHECKING:
    from policy_compare.engine import Analysis

MAX_ACTIONS = 6
MAX_REQUIREMENT_ACTIONS = 2
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


@dataclass
class Rec:
    priority: str                    # high | medium | low
    audience: str                    # carrier | client | internal
    action: str
    reason: str
    finding_ids: list[str] = field(default_factory=list)
    source: str = "default"


def _priority_cap(fs: list[Finding]) -> str:
    """The most urgent priority the cited findings support."""
    if any(f.severity in ("critical", "high") or f.impact == "reduced" for f in fs):
        return "high"
    if any(f.severity == "medium" for f in fs):
        return "medium"
    return "low"


def evidence(an: "Analysis") -> list[Finding]:
    """Findings an action may rest on: material changes, items to confirm, observations and requirements that no
    form on either policy meets."""
    out = [f for f in an.unique_changes() if f.impact in ("reduced", "confirm", "improved")]
    out += an.findings.get("observations", [])
    out += [f for f in an.findings.get("checklist", []) if f.kind == "contract_slot" and not f.changed
            and f.context.get("expiring_status") == "not_found" and f.context.get("renewal_status") == "not_found"]
    seen, uniq = set(), []
    for f in out:
        if f.id not in seen:
            seen.add(f.id)
            uniq.append(f)
    return uniq


def _fact(f: Finding) -> dict:
    d = {"id": f.id, "section": SECTION_TITLES[f.section], "item": f.label, "expiring": f.exp, "renewal": f.ren,
         "change": f.change, "impact": f.impact or "unchanged", "severity": f.severity}
    for k in ("form_role", "forms_this_form_amends", "forms_that_amend_this_form", "amended_by", "quote", "replaced_by"):
        if f.context.get(k):
            d[k] = f.context[k]
    if f.section == "observations":
        d["note"] = "unchanged at renewal; the term matters for this client"
    if f.kind == "contract_slot":
        d["note"] = "no form on either policy meets this common contract requirement"
    return d


def default_recs(an: "Analysis") -> list[Rec]:
    recs: list[Rec] = []
    crit = {f.id: f for f in an.critical_candidates()}
    for fid in an.narratives.critical_order:
        f, n = crit.get(fid), an.narratives.critical.get(fid)
        if f and n:
            recs.append(Rec("high", "carrier", n.next_step, f"{f.label}: {f.change[:1].lower() + f.change[1:]}.", [f.id]))
    covered = {i for r in recs for i in r.finding_ids}
    for f in an.findings.get("pending", []):
        src = f.duplicate_of
        if any(g.change_key == src and g.id in covered for g in crit.values()):
            continue
        recs.append(Rec(_priority_cap([f]) if f.severity != "low" else "low", "carrier", f.change,
                        f"{f.label} is on the expiring policy but not on the renewal.", [f.id]))
    for f in an.findings.get("observations", []):
        recs.append(Rec("medium" if f.severity in ("critical", "high") else "low", "client", _sentence(f.change),
                        f"{f.label}; this did not change at renewal.", [f.id]))
    for f in [f for f in evidence(an) if f.kind == "contract_slot"][:MAX_REQUIREMENT_ACTIONS]:
        req = f.label.split(": ", 1)[-1]
        recs.append(Rec("low", "internal", f"Check whether the client's contracts require {req.lower()}.",
                        "No form on either policy provides it.", [f.id]))
    improved = [f for f in an.unique_changes() if f.impact == "improved"]
    if improved:
        recs.append(Rec("low", "client", "Tell the client which terms improve at renewal.",
                        "; ".join(f"{f.label}: {f.change.lower()}" for f in improved[:3]) + ".", [f.id for f in improved[:3]]))
    return _order(recs)[:MAX_ACTIONS]


def _sentence(t: str) -> str:
    t = (t or "").strip()
    return t if t.endswith((".", "!", "?")) else t + "."


def _order(recs: list[Rec]) -> list[Rec]:
    return sorted(recs, key=lambda r: PRIORITY_RANK.get(r.priority, 9))


def recommend(llm: Optional[LLMClient], an: "Analysis") -> list[Rec]:
    defaults = default_recs(an)
    ev = evidence(an)
    if not llm or not ev:
        return defaults
    from policy_compare.analysis.assess import client_profile
    user = prompt("recommend").format(
        profile=json.dumps(client_profile(an), ensure_ascii=False, indent=1), deadline=an.deadline,
        evidence=json.dumps([_fact(f) for f in ev], ensure_ascii=False, indent=1), max_items=MAX_ACTIONS)
    try:
        res = llm.structured("recommend", user, Recommendations)
    except LLMError as e:
        an.warnings.append(f"recommended actions used rule-based text ({e})")
        return defaults
    by_id = {f.id: f for f in ev}
    gate = TextGate(an)
    log = an.audit.setdefault("recommendations", {"dropped": []})
    out: list[Rec] = []
    for a in res.actions:
        cited = [by_id[i] for i in dict.fromkeys(a.evidence_ids) if i in by_id]
        if not cited:
            log["dropped"].append({"action": a.action, "reason": "cites no finding of the comparison"})
            continue
        action = gate.take("recommended action", a.action, 22)
        reason = gate.take("recommended action reason", a.reason, 34)
        if not (action and reason):
            log["dropped"].append({"action": a.action, "reason": "text rejected by the gate"})
            continue
        cap = _priority_cap(cited)
        prio = a.priority if PRIORITY_RANK[a.priority] >= PRIORITY_RANK[cap] else cap
        out.append(Rec(prio, a.audience, action, reason, [f.id for f in cited], source="model"))
        if len(out) >= MAX_ACTIONS:
            break
    if not out:
        return defaults
    # every critical change keeps an action, even if the model left it out
    covered = {i for r in out for i in r.finding_ids}
    for r in defaults:
        if r.priority == "high" and not covered & set(r.finding_ids):
            out.append(r)
    log["source"] = "model"
    return _order(out)[:MAX_ACTIONS + 2]


def refs_for(an: "Analysis", rec: Rec) -> list[str]:
    """Page references of the cited findings (expiring first), at most three."""
    by_id = {f.id: f for f in an.all_findings()}
    refs: list[str] = []
    for i in rec.finding_ids:
        f = by_id.get(i)
        for r in (f.exp_ref, f.ren_ref) if f else ():
            if r and r not in refs:
                refs.append(r)
    return refs[:3]
