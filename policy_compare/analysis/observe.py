"""A: coverage observations — exclusions and limitations that did NOT change at renewal but matter for this client.

The model reviews the unchanged restrictive forms against the client profile. Every observation must name a form
that is on both policies and carry a verbatim quote from that form's text, or it is dropped.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from policy_compare.analysis.guards import TextGate
from policy_compare.diff import Ids
from policy_compare.findings import SEVERITY_RANK, Finding
from policy_compare.forms import form_role
from policy_compare.llm.client import LLMClient, LLMError, prompt
from policy_compare.schema.llm_io import ObservationSet
from policy_compare.settings import config
from policy_compare.textindex import MIN_QUOTE_WORDS, form_key, norm_space

if TYPE_CHECKING:
    from policy_compare.engine import Analysis

MAX_OBSERVATIONS = 6
EXCERPT_CHARS = 700


def candidate_forms(an: "Analysis") -> list:
    """Restrictive forms present on both policies (changed forms are already covered in Section 7)."""
    rub = config("rubric")["forms"]
    out = []
    for k, f in an.fr.items():
        if k not in an.fe or not f.doc:
            continue
        if form_role(f, rub).startswith("exclusion") or "limitation" in f.title.lower():
            out.append(f)
    return out[:18]


def _page_in_doc(page, doc) -> bool:
    return bool(page) and any(p.page_number == page for p in doc.pages)


def observe(llm: LLMClient, an: "Analysis", ids: Ids) -> list[Finding]:
    from policy_compare.analysis.assess import client_profile, section_from

    cands = candidate_forms(an)
    if not cands:
        an.audit["observations_ran"] = True
        return []
    items = [{"form_number": f.number, "title": f.title,
              "excerpt": norm_space(an.ti_r.document_text(f.doc))[:EXCERPT_CHARS]} for f in cands]
    user = prompt("observations").format(profile=json.dumps(client_profile(an), ensure_ascii=False, indent=1),
                                         forms=json.dumps(items, ensure_ascii=False, indent=1), max_items=MAX_OBSERVATIONS)
    try:
        res = llm.structured("observations", user, ObservationSet)
    except LLMError as e:
        an.warnings.append(f"coverage observations failed; section left empty ({e})")
        return []
    an.audit["observations_ran"] = True

    gate = TextGate(an)
    by_key = {form_key(f.number): f for f in cands}
    rejected = an.audit.setdefault("rejected_text", [])
    out: list[Finding] = []
    for o in sorted(res.observations, key=lambda o: SEVERITY_RANK.get(o.severity, 9)):
        f = by_key.get(form_key(o.form_number))
        if not f:
            rejected.append({"field": "observation", "text": o.concern, "reason": f"form {o.form_number} is not an unchanged restrictive form"})
            continue
        page = an.ti_r.contains_verbatim(o.quote, MIN_QUOTE_WORDS)
        if not _page_in_doc(page, f.doc):
            rejected.append({"field": f"observation {f.number}", "text": o.quote, "reason": "quote is not verbatim in that form's text"})
            continue
        concern = gate.take(f"observation {f.number} concern", o.concern, 14, kind="bullet")
        why = gate.take(f"observation {f.number} why", o.why, 50)
        rec = gate.take(f"observation {f.number} recommendation", o.recommendation, 20)
        if not (concern and why and rec):
            continue
        quote = o.quote.strip().strip("“”\"")
        out.append(Finding(
            id=ids.next(), change_key=f"observe:{f.key}", section="observations", kind="observation",
            label=concern.rstrip("."), sublabel=f"{f.number} — {f.title}",
            exp=an.fe[f.key].display, exp_ref=f"{an.E.ref} p.{an.fe[f.key].page}" if an.fe[f.key].page else None,
            ren=f.display, ren_ref=f"{an.R.ref} p.{f.page}" if f.page else None,
            change=rec, impact="confirm", severity=o.severity, locked=False,
            why=f"{why} “{quote}” [[{an.R.ref} p.{page}]]", why_source="model", type="observation",
            context={"form_number": f.number, "title": f.title, "quote": quote, "why_text": why,
                     "evidence": f"“{quote}” [[{an.R.ref} p.{page}]]"}))
        if len(out) >= MAX_OBSERVATIONS:
            break
    n = section_from(res, "observations", out, an, gate, allow_reduction_words=True)
    if len(out) < len(res.observations):   # the model's summary may describe observations that were dropped
        from policy_compare.analysis.narrative import default_section
        from policy_compare.engine import narrative_context
        d = default_section("observations", out, {**narrative_context(an), "observations_ran": True})
        n.headline, n.bullets, n.takeaway = d.headline, d.bullets, d.takeaway
    an.narratives.sections["observations"] = n
    return out
