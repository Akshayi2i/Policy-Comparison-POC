"""Model stage 2: executive summary, critical-change ranking, cross-reference section texts, focus quotes."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from policy_compare.analysis.guards import allowed_numbers, clean_text
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
    allowed = allowed_numbers(an)

    # executive
    d = an.narratives.executive
    an.narratives.executive = ExecNarrative(
        risk=res.risk, confidence=res.confidence,
        lead=clean_text(res.lead, max_words=16, allowed=allowed) or d.lead,
        body=clean_text(res.body, max_words=70, allowed=allowed, forbid=["critical change"]) or d.body,
        confidence_note=clean_text(res.confidence_note, max_words=40, allowed=allowed) or d.confidence_note, source="model")

    # critical ranking: only real candidates; every critical-severity candidate kept; severity tiers enforced
    by_id = {f.id: f for f in cands}
    chosen: list[str] = []
    for item in res.critical:
        f = by_id.get(item.id)
        if not f or item.id in chosen:
            continue
        dflt = default_critical(f, an.deadline)
        an.narratives.critical[item.id] = CriticalNarrative(
            title=clean_text(item.title, max_words=8, allowed=allowed) or dflt.title, theme=item.theme,
            description=clean_text(item.description, max_words=18, allowed=allowed) or dflt.description,
            next_step=clean_text(item.next_step, max_words=18, allowed=allowed) or dflt.next_step, source="model")
        chosen.append(item.id)
    for f in cands:
        if f.severity == "critical" and f.id not in chosen:
            an.narratives.critical.setdefault(f.id, default_critical(f, an.deadline))
            chosen.append(f.id)
    chosen.sort(key=lambda i: SEVERITY_RANK[by_id[i].severity])
    an.narratives.critical_order = chosen[:max_items]

    # cross-reference sections
    for s, txt in (("checklist", res.checklist), ("focus", res.focus), ("pending", res.pending)):
        dflt = default_section(s, an.findings[s], {**ctx, "topics": [t.topic for t in an.focus]})
        bullets = [b for b in (clean_text(x, max_words=18, allowed=allowed) for x in txt.bullets[:3]) if b]
        an.narratives.sections[s] = SectionNarrative(
            headline=clean_text(txt.headline, max_words=30, allowed=allowed) or dflt.headline, bullets=bullets or dflt.bullets,
            takeaway=clean_text(txt.takeaway, max_words=25, allowed=allowed) or dflt.takeaway, risk_level=txt.risk_level,
            risk_statement=clean_text(txt.risk_statement, max_words=36, allowed=allowed) or dflt.risk_statement,
            confidence=txt.confidence, confidence_reason=clean_text(txt.confidence_reason, max_words=36, allowed=allowed) or dflt.confidence_reason,
            source="model")

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
