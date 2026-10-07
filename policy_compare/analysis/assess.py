"""Model stage 1: classify unknown fields, extract mid-term changes, assess each value section."""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from policy_compare.analysis.guards import allowed_numbers, clean_text
from policy_compare.analysis.narrative import SECTION_TITLES, SectionNarrative, default_section
from policy_compare.diff import Ids
from policy_compare.findings import Finding
from policy_compare.flatten import Cell, Element
from policy_compare.fmt import mdy
from policy_compare.ingest import Side
from policy_compare.llm.client import LLMClient, LLMError, prompt
from policy_compare.schema.llm_io import MidtermExtraction, PathClassification, SectionAssessment, TopicPick
from policy_compare.textindex import TextIndex, norm_key, norm_space

if TYPE_CHECKING:
    from policy_compare.engine import Analysis

log = logging.getLogger("policy_compare")
ASSESSED = ["policy", "premium", "limits", "terms", "forms", "midterm"]
WHY_HINTS = {
    "forms": 'For forms, "why" says what the form does and what its removal, addition or change means for the insured; '
             'base it on the title and "form_text_excerpt" (or the changed passages).',
    "midterm": 'For mid-term changes, "why" says whether the change carried into the renewal and what that means.',
}


def _dump(x) -> str:
    return json.dumps(x, ensure_ascii=False, indent=1)


def policy_context(an: "Analysis") -> str:
    E, R = an.E, an.R
    return _dump({
        "insured": R.insured or E.insured,
        "expiring": {"carrier": E.carrier, "policy_number": E.model.policy.policy_number, "period": f"{mdy(E.effective)} to {mdy(E.expiration)}",
                     "policy_type": E.model.policy.policy_type},
        "renewal": {"carrier": R.carrier, "policy_number": R.model.policy.policy_number, "period": f"{mdy(R.effective)} to {mdy(R.expiration)}",
                    "policy_type": R.model.policy.policy_type},
    })


def classify_unmatched(llm: LLMClient, unmatched: list[Element], e_el: dict[str, Element], r_el: dict[str, Element]) -> None:
    items = []
    for el in unmatched[:60]:
        other = r_el.get(el.key) or e_el.get(el.key)
        items.append({"pattern": el.pattern, "label": el.label, "group": el.group,
                      "sample_value": el.cell.text, "other_side_value": other.cell.text if other else None})
    try:
        res = llm.structured("classify_paths", prompt("classify_paths").format(items=_dump(items)), PathClassification)
    except LLMError as e:
        log.warning("path classification skipped: %s", e)
        return
    by_pattern = {i.pattern: i for i in res.items}
    for side in (e_el, r_el):
        for el in side.values():
            c = by_pattern.get(el.pattern)
            if c and el.classified_by == "default":
                el.section, el.type, el.classified_by = c.section, c.type, "model"
                label = clean_text(c.label, max_words=8)
                if label:
                    el.label = label


def assess_sections(llm: LLMClient, an: "Analysis") -> None:
    allowed = allowed_numbers(an)
    ctx_text = policy_context(an)
    for s in ASSESSED:
        rows = an.findings.get(s, [])
        changed = [f for f in rows if f.changed]
        if not changed:
            continue
        items = [f.facts() for f in changed] + \
                [{"id": f.id, "label": f.label, "expiring": f.exp, "renewal": f.ren, "change": "No change"}
                 for f in rows if not f.changed and f.always_show][:12]
        user = prompt("section").format(number=str(["policy", "premium", "limits", "terms", "forms", "midterm"].index(s) + 3),
                                        title=SECTION_TITLES[s], context=ctx_text, items=_dump(items),
                                        why_hint=WHY_HINTS.get(s, ""))
        try:
            res = llm.structured(f"section_{s}", user, SectionAssessment)
        except LLMError as e:
            an.warnings.append(f"model assessment of section '{s}' failed; using rule-based text ({e})")
            continue
        by_id = {f.id: f for f in changed}
        for item in res.items:
            f = by_id.get(item.id)
            if not f:
                an.audit.setdefault("guards", []).append(f"{s}: model referenced unknown id {item.id}")
                continue
            if not f.locked:
                f.impact, f.severity = item.impact, item.severity
            why = clean_text(item.why, max_words=60, allowed=allowed)
            if why:
                if f.kind == "form_wording" and f.why and "[[" in f.why:
                    f.why = f.why.split("]]. ")[0] + "]]. " + why      # keep the verbatim passage, replace the comment
                else:
                    f.why = why
                f.why_source = "model"
        an.narratives.sections[s] = _section_from(res, s, rows, an, allowed)


def _section_from(res, s: str, rows: list[Finding], an: "Analysis", allowed: set[str]) -> SectionNarrative:
    from policy_compare.engine import narrative_context
    d = default_section(s, rows, narrative_context(an))
    bullets = [b for b in (clean_text(x, max_words=18, allowed=allowed) for x in res.bullets[:3]) if b]
    return SectionNarrative(
        headline=clean_text(res.headline, max_words=30, allowed=allowed) or d.headline,
        bullets=bullets or d.bullets,
        takeaway=clean_text(res.takeaway, max_words=25, allowed=allowed) or d.takeaway,
        risk_level=res.risk_level, risk_statement=clean_text(res.risk_statement, max_words=36, allowed=allowed) or d.risk_statement,
        confidence=res.confidence, confidence_reason=clean_text(res.confidence_reason, max_words=36, allowed=allowed) or d.confidence_reason,
        source="model")


def pick_topics(llm: LLMClient, an: "Analysis") -> list[str]:
    changes = [{"label": f.label, "change": f.change, "impact": f.impact, "severity": f.severity,
                "title": f.context.get("title")} for f in an.all_findings() if f.changed][:40]
    if not changes:
        return []
    try:
        res = llm.structured("topics", prompt("topics").format(changes=_dump(changes)), TopicPick)
    except LLMError:
        return []
    out = []
    for t in res.topics[:3]:
        t = (clean_text(t, max_words=4) or "").lower().strip(" .\"'“”")
        if t and (an.ti_e.count(t) or an.ti_r.count(t)) and t not in out:
            out.append(t)
    return out


# ---------------- mid-term changes ----------------

def extract_midterm(llm: LLMClient, E: Side, R: Side, ti_e: TextIndex, e_el: dict[str, Element], r_el: dict[str, Element],
                    detected: list[Finding], ids: Ids) -> list[Finding]:
    """Turn detected policy-change documents into verified change rows and amend the expiring values."""
    from policy_compare.xref import midterm_documents
    out: list[Finding] = []
    for doc in midterm_documents(E)[:4]:
        text = ti_e.document_text(doc)
        try:
            res = llm.structured("midterm", prompt("midterm").format(title=doc.title or doc.form_number, text=norm_space(text)[:6000]),
                                 MidtermExtraction)
        except LLMError:
            out.extend(f for f in detected if (doc.form_number or doc.title) in f.change_key)
            continue
        for ch in res.changes:
            page = ti_e.contains_verbatim(ch.quote)
            if not page:   # the quote must be verbatim, otherwise the change is not trusted
                continue
            label = f"Policy change · {ch.endorsement}" + (f" · eff. {ch.effective_date}" if ch.effective_date else "")
            el = _match_element(ch.item, e_el)
            after_amt = _amount(ch.after)
            f = Finding(id=ids.next(), change_key=f"midterm:{norm_key(ch.endorsement)}:{norm_key(ch.item)}", section="midterm",
                        kind="midterm", label=label, exp=ch.after, exp_ref=f"{E.ref} p.{page}", ren="", type="midterm",
                        context={"item": ch.item, "before": ch.before, "after": ch.after, "quote": ch.quote})
            if el and after_amt is not None and el.cell.kind == "amount":
                before = el.cell.text
                ren_el = r_el.get(el.key)
                el.cell = Cell(kind="amount", amount=after_amt, text=ch.after if "$" in ch.after else f"${after_amt:,.0f}", raw=ch.after)
                el.amended, el.page_hint = True, page
                f.change_key = f"value:{el.key}"
                if ren_el and ren_el.cell.amount is not None:
                    f.ren, f.ren_ref = ren_el.cell.text or "—", None
                    if abs(ren_el.cell.amount - after_amt) < 0.5:
                        f.change = "Carried into renewal"
                    else:
                        f.impact = "reduced" if (ren_el.cell.amount < after_amt and el.type == "limit") or \
                                                (ren_el.cell.amount > after_amt and el.type == "deductible") else "confirm"
                        f.severity, f.locked = "high", False
                        f.change = "Mid-term change not carried into renewal"
                        f.why = "The renewal reverts to the original declarations; ask the underwriter to carry the change forward."
                out.append(Finding(id=ids.next(), change_key=f"midterm-base:{el.key}", section="midterm", kind="midterm_base",
                                   label=f"{el.label} — Declarations", exp=before or "—", ren="", change="Stated on Declarations"))
            else:
                f.impact, f.severity, f.locked = "confirm", "low", False
                f.change = "Applied - item not matched on the policy schedule"
                f.why = "Confirm with the expiring carrier what this change applied to."
            out.append(f)
    return out


def _amount(s: str):
    m = re.search(r"\$?\s*([\d,]+(?:\.\d+)?)", s or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _match_element(item: str, els: dict[str, Element]):
    words = [w for w in norm_key(item).split() if len(w) > 2]
    if not words:
        return None
    best, score = None, 0
    for el in els.values():
        if el.section == "ignore":
            continue
        lab = norm_key(el.label)
        s = sum(w in lab for w in words)
        if s > score:
            best, score = el, s
    return best if best and score >= max(1, len(words) - 1) else None
