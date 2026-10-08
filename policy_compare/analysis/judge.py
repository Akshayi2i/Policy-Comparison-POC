"""G: second pass — the model re-reads its own narrative against the facts.

Every model-written statement gets an id; the model lists the ones the facts do not support. Those are reverted to
the rule-based text (observations are dropped) and logged in the audit. Rule-based text is never judged.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from policy_compare.analysis.narrative import SECTION_TITLES, default_critical, default_exec, default_section
from policy_compare.llm.client import LLMClient, LLMError, prompt
from policy_compare.schema.llm_io import JudgeResult

if TYPE_CHECKING:
    from policy_compare.engine import Analysis


@dataclass
class Statement:
    id: str
    field: str
    text: str
    revert: Callable[[], None]


def collect(an: "Analysis") -> list[Statement]:
    from policy_compare.engine import narrative_context
    out: list[Statement] = []
    ctx = {**narrative_context(an), "topics": [t.topic for t in an.focus],
           "observations_ran": an.audit.get("observations_ran", False)}

    def add(field: str, text, revert, default=None):
        if text and text != default:                  # rule-based text inside a model section is not judged
            out.append(Statement(f"s{len(out) + 1:02d}", field, text, revert))

    ex = an.narratives.executive
    if ex and ex.source == "model":
        crit = [f for f in an.critical_candidates() if f.id in an.narratives.critical_order]
        d = default_exec(an.narratives.sections, an.unique_changes(), crit, an.premium(), an.deadline)
        add("executive lead", ex.lead, lambda ex=ex, d=d: setattr(ex, "lead", d.lead), d.lead)
        add("executive body", ex.body, lambda ex=ex, d=d: setattr(ex, "body", d.body), d.body)
    for s, n in an.narratives.sections.items():
        if n.source != "model":
            continue
        d = default_section(s, an.findings.get(s, []), ctx)
        name = SECTION_TITLES[s]
        add(f"{name} headline", n.headline, lambda n=n, d=d: setattr(n, "headline", d.headline), d.headline)
        add(f"{name} takeaway", n.takeaway, lambda n=n, d=d: setattr(n, "takeaway", d.takeaway), d.takeaway)
        add(f"{name} risk statement", n.risk_statement,       # the level goes with its statement
            lambda n=n, d=d: (setattr(n, "risk_statement", d.risk_statement), setattr(n, "risk_level", d.risk_level)),
            d.risk_statement)
        for i, b in enumerate(n.bullets):
            if b in d.bullets:
                continue
            add(f"{name} bullet", b,
                lambda n=n, b=b, d=d: setattr(n, "bullets", [x for x in n.bullets if x != b] or d.bullets))
    for f in an.all_findings():
        if f.section == "observations":     # the row is verified by code; only its explanation can be withdrawn
            add(f"observation {f.label} explanation", f.context.get("why_text"),
                lambda f=f: setattr(f, "why", f.context.get("evidence") or f.why))
        elif f.why_source == "model" and f.why:
            add(f"{f.label} — {f.explain_label}", f.why, lambda f=f: _withdraw_why(an, f))
    for r in [r for r in an.recommendations if r.source == "model"]:
        add(f"recommended action: {r.action}", f"{r.action} {r.reason}",
            lambda r=r: an.recommendations.remove(r) if r in an.recommendations else None)
    crit = {f.id: f for f in an.critical_candidates()}
    for fid, n in an.narratives.critical.items():
        if n.source == "model" and fid in crit:
            d = default_critical(crit[fid], an.deadline)
            add(f"critical {crit[fid].label}", f"{n.title}: {n.description} Next step: {n.next_step}",
                lambda n=n, d=d: (setattr(n, "title", d.title), setattr(n, "description", d.description),
                                  setattr(n, "next_step", d.next_step)))
    return out


def _withdraw_why(an: "Analysis", f) -> None:
    """Back to the rule-based explanation, also where later sections copied the model's text (e.g. Section 11)."""
    old = f.why
    f.why, f.why_source = f.why_default, "template"
    for g in an.all_findings():
        if g is not f and g.sublabel == old:
            g.sublabel = None


def facts(an: "Analysis") -> list[dict]:
    """Numbered facts (F1, F2, ...) the second pass must cite when it calls a statement unsupported."""
    from policy_compare.analysis.assess import client_profile
    from policy_compare.fmt import money
    from policy_compare.forms import amended_by, form_summary
    lines: list[str] = []
    p = client_profile(an)
    lines.append("Client: " + "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in p.items() if v))
    prem = an.premium()
    if prem:
        lines.append(f"Premium: expiring {money(prem[0])}, renewal {money(prem[1])}.")
    for f in an.unique_changes():
        lines.append(f"Change: {f.label}: {f.change} (impact {f.impact}, severity {f.severity}).")
    for f in an.findings.get("forms", []):
        if not f.changed:
            continue
        key = f.change_key.split(":", 1)[1].split(">")[0]
        form = an.fe.get(key) or an.fr.get(key)
        ti = an.ti_e if key in an.fe else an.ti_r
        said = form_summary(ti, form.doc) if form else None
        extra = [f"role: {f.context['form_role']}"] if f.context.get("form_role") else []
        for k in ("forms_this_form_amends", "forms_that_amend_this_form", "replaced_by"):
            if f.context.get(k):
                extra.append(f"{k.replace('_', ' ')}: {f.context[k]}")
        if said:
            extra.append(f"the form says: “{said[0]}”")
        lines.append(f"Form change: {f.label}: {f.change}; " + "; ".join(extra) + ".")
    both = [f"{f.number} — {f.title}" for k, f in an.fr.items() if k in an.fe]
    if both:
        lines.append("On both policies (unchanged): " + "; ".join(both) + ".")
    for k, f in an.fr.items():
        by = amended_by(f, an.fr)
        if by and k in an.fe:
            lines.append(f"{f.number} is amended by {', '.join(o.number + ' — ' + o.title for o in by)} (on the renewal).")
    for f in an.findings.get("observations", []):
        lines.append(f"Observation (unchanged term): {f.context.get('form_number')} says “{f.context.get('quote')}”.")
    if not any(f.kind == "form_edition" for f in an.findings.get("forms", [])):
        lines.append("No form edition changed.")
    for s in ("limits", "premium", "terms"):
        if not any(f.changed for f in an.findings.get(s, [])):
            lines.append(f"No {s} value changed.")
    return [{"id": f"F{i}", "fact": t} for i, t in enumerate(lines, 1)]


def judge(llm: LLMClient, an: "Analysis") -> None:
    statements = collect(an)
    if not statements:
        return
    fs = facts(an)
    user = prompt("judge").format(facts=json.dumps(fs, ensure_ascii=False, indent=1),
                                  statements=json.dumps([{"id": s.id, "text": s.text} for s in statements],
                                                        ensure_ascii=False, indent=1))
    try:
        res = llm.structured("judge", user, JudgeResult)
    except LLMError as e:
        an.warnings.append(f"second-pass check failed; narrative kept as validated by the rules ({e})")
        return
    by_id = {s.id: s for s in statements}
    fact_by_id = {f["id"]: f["fact"] for f in fs}
    log = an.audit.setdefault("judge", [])
    for issue in res.unsupported:
        st = by_id.get(issue.id)
        if not st:
            continue
        if issue.contradicted_by not in fact_by_id:      # no contradicting fact named: the statement stays
            an.audit.setdefault("judge_ignored", []).append({"field": st.field, "reason": issue.reason})
            continue
        st.revert()
        log.append({"field": st.field, "text": st.text, "reason": issue.reason,
                    "fact": fact_by_id[issue.contradicted_by]})
    an.audit["judge_checked"] = len(statements)
