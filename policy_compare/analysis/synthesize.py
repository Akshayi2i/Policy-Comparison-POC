"""Model stage 2: executive summary, critical-change ranking, cross-reference section texts, focus quotes."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from policy_compare.analysis.guards import TextGate
from policy_compare.analysis.narrative import (
    SECTION_TITLES, CriticalNarrative, ExecNarrative, SectionNarrative, default_critical, default_section,
)
from policy_compare.findings import SEVERITY_RANK
from policy_compare.fmt import mdy, money
from policy_compare.llm.client import LLMClient, LLMError, prompt
from policy_compare.schema.llm_io import Synthesis

if TYPE_CHECKING:
    from policy_compare.engine import Analysis


def _dump(x) -> str:
    return json.dumps(x, ensure_ascii=False, indent=1)


def synthesize(llm: LLMClient, an: "Analysis", ctx: dict) -> None:
    from policy_compare.analysis.assess import policy_context
    from policy_compare.settings import config

    max_items = config("rubric")["critical_changes"]["max_items"]
    unique = an.unique_changes()
    cands = an.critical_candidates()
    prem = an.premium()
    sections = [{"number": str(i + 3), "title": SECTION_TITLES[s], "headline": an.narratives.sections[s].headline,
                 "risk": an.narratives.sections[s].risk_level}
                for i, s in enumerate(["policy", "premium", "limits", "terms", "forms", "midterm"]) if s in an.narratives.sections]
    focus = [{"topic": t.topic, "mentions_expiring": t.mentions_e, "mentions_renewal": t.mentions_r,
              "related_changes": [{"label": r.label, "change": r.change, "impact": r.impact, "severity": r.severity} for r in t.related[:4]],
              "expiring_quotes": [{"i": i, "page": s.page, "text": s.text[:220]} for i, s in enumerate(t.candidates_e[:5])],
              "renewal_quotes": [{"i": i, "page": s.page, "text": s.text[:220]} for i, s in enumerate(t.candidates_r[:5])]}
             for t in an.focus]
    context = json.loads(policy_context(an))
    if prem:
        context["premium"] = {"expiring": money(prem[0]), "renewal": money(prem[1]), "change": money(prem[1] - prem[0])}
    context["expiry_deadline"] = an.deadline
    context["form_changes"] = [{"form": f.label, "change": f.change, "form_role": f.context.get("form_role"),
                                "related_forms_still_on_renewal": f.context.get("related_forms_still_on_renewal"),
                                "related_forms_on_expiring": f.context.get("related_forms_on_expiring"),
                                "replaced_by": f.context.get("replaced_by")}
                               for f in an.findings["forms"] if f.changed]
    context["forms_on_both_policies"] = [f"{f.number} — {f.title}" for k, f in an.fr.items() if k in an.fe]
    context["coverage_observations_no_change"] = [f"{f.label} ({f.sublabel})" for f in an.findings.get("observations", [])]
    user = prompt("synthesis").format(
        insured=an.R.insured or an.E.insured, context=_dump(context), sections=_dump(sections),
        changes=_dump([{"id": f.id, "label": f.label, "change": f.change, "impact": f.impact, "severity": f.severity,
                        "section": SECTION_TITLES[f.section]} for f in unique][:60]),
        candidates=_dump([f.facts() for f in cands]),
        checklist=_dump([f.facts() for f in an.findings["checklist"]]),
        focus=_dump(focus), pending=_dump([f.facts() for f in an.findings["pending"]]), max_items=max_items)
    try:
        res = llm.structured("synthesis", user, Synthesis)
    except LLMError as e:
        an.warnings.append(f"model synthesis failed; executive summary uses rule-based text ({e})")
        return
    gate = TextGate(an)
    before = len(an.audit.get("rejected_text", []))

    # executive
    nr = not any(f.impact == "reduced" for f in unique)
    d = an.narratives.executive
    an.narratives.executive = ExecNarrative(
        risk=res.risk, confidence=res.confidence,
        lead=gate.take("executive lead", res.lead, 16, kind="headline", no_reduction=nr) or d.lead,
        body=gate.take("executive body", res.body, 70, forbid=["critical change"], no_reduction=nr) or d.body,
        confidence_note=gate.take("executive confidence note", res.confidence_note, 40) or d.confidence_note, source="model")
    if any(r["reason"].startswith("contradicts") for r in an.audit.get("rejected_text", [])[before:]):
        an.narratives.executive.risk, an.narratives.executive.confidence = d.risk, d.confidence
        an.audit.setdefault("guards", []).append("executive: model text contradicted the facts; rule-based risk used")

    # critical ranking: only real candidates; every critical-severity candidate kept; severity tiers enforced
    by_id = {f.id: f for f in cands}
    chosen: list[str] = []
    for item in res.critical:
        f = by_id.get(item.id)
        if not f or item.id in chosen:
            continue
        dflt = default_critical(f, an.deadline)
        an.narratives.critical[item.id] = CriticalNarrative(
            title=gate.take(f"critical {f.label} title", item.title, 8) or dflt.title, theme=item.theme,
            description=gate.take(f"critical {f.label} description", item.description, 18) or dflt.description,
            next_step=gate.take(f"critical {f.label} next step", item.next_step, 18) or dflt.next_step, source="model")
        chosen.append(item.id)
    for f in cands:
        if f.severity == "critical" and f.id not in chosen:
            an.narratives.critical.setdefault(f.id, default_critical(f, an.deadline))
            chosen.append(f.id)
    chosen.sort(key=lambda i: SEVERITY_RANK[by_id[i].severity])
    an.narratives.critical_order = chosen[:max_items]

    # cross-reference sections (same gate and fallback rules as the value sections)
    from policy_compare.analysis.assess import section_from
    for s, txt in (("checklist", res.checklist), ("focus", res.focus), ("pending", res.pending)):
        an.narratives.sections[s] = section_from(txt, s, an.findings[s], an, gate)

    # focus quotes: indexes must point at real candidate sentences
    topics = {t.topic: t for t in an.focus}
    for q in res.focus_quotes:
        t = topics.get(q.topic)
        if not t:
            continue
        sel = [("expiring", i) for i in q.expiring[:2] if 0 <= i < min(5, len(t.candidates_e))] + \
              [("renewal", i) for i in q.renewal[:2] if 0 <= i < min(5, len(t.candidates_r))]
        if sel:
            an.narratives.focus_quotes[q.topic] = sel
