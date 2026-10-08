"""Model stage 1: classify unknown fields, extract mid-term changes, assess each value section."""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from policy_compare.analysis.guards import TextGate, clean_text
from policy_compare.analysis.narrative import SECTION_TITLES, SectionNarrative, default_section
from policy_compare.diff import Ids
from policy_compare.findings import Finding
from policy_compare.flatten import Cell, Element
from policy_compare.fmt import mdy
from policy_compare.ingest import Side
from policy_compare.llm.client import LLMClient, LLMError, prompt
from policy_compare.schema.llm_io import MidtermExtraction, PathClassification, SectionAssessment, TopicPick
from policy_compare.textindex import MIN_QUOTE_WORDS, TextIndex, norm_key, norm_space

if TYPE_CHECKING:
    from policy_compare.engine import Analysis

log = logging.getLogger("policy_compare")
ASSESSED = ["policy", "premium", "limits", "terms", "forms", "midterm"]
WHY_HINTS = {
    "forms": 'For forms, "why" is "What it does": first state in plain words what the form does, based on its '
             '"form_text_excerpt" (not on guesses from the title), then what its removal, addition or change means for '
             'the insured. Use "form_role": a notice or disclosure form is informational. If '
             '"related_forms_still_on_renewal" lists a form on the same subject, say that cover on that subject '
             'continues under that form.',
    "midterm": 'For mid-term changes, "why" says whether the change carried into the renewal and what that means.',
}


def _dump(x) -> str:
    return json.dumps(x, ensure_ascii=False, indent=1)


def client_profile(an: "Analysis") -> dict:
    """What the client does and buys — used to judge how much each change matters to *this* client."""
    E, R = an.E, an.R
    extra = R.model.policy.model_extra or {}
    parts = next((f.ren for f in an.findings.get("policy", []) if f.kind == "coverage_parts"), "")
    return {
        "insured": R.insured or E.insured,
        "class_description": extra.get("class_description") or (E.model.policy.model_extra or {}).get("class_description"),
        "policy_type": R.model.policy.policy_type or E.model.policy.policy_type,
        "coverage_parts": parts,
        "location_state": ((R.data.get("named_insured") or {}).get("mailing_address") or {}).get("state"),
    }


def policy_context(an: "Analysis") -> str:
    E, R = an.E, an.R
    return _dump({
        "client_profile": client_profile(an),
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


def forms_context(an: "Analysis") -> str:
    """The forms that did not change, so the model can tell a removed notice from a removed exclusion."""
    both = [f"{f.number} — {f.title}" for k, f in an.fr.items() if k in an.fe]
    return "Forms on BOTH policies (unchanged; never describe these as removed or added):\n" + _dump(both)


def assess_sections(llm: LLMClient, an: "Analysis") -> None:
    gate = TextGate(an)
    ctx_text = policy_context(an)
    for s in ASSESSED:
        rows = an.findings.get(s, [])
        changed = [f for f in rows if f.changed]
        if not changed:
            continue
        items = [f.facts() for f in changed] +                 [{"id": f.id, "label": f.label, "expiring": f.exp, "renewal": f.ren, "change": "No change"}
                 for f in rows if not f.changed and f.always_show][:12]
        user = prompt("section").format(number=str(["policy", "premium", "limits", "terms", "forms", "midterm"].index(s) + 3),
                                        title=SECTION_TITLES[s], context=ctx_text, items=_dump(items),
                                        extra=forms_context(an) if s == "forms" else "",
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
            why = gate.take(f"§{f.number} {f.label} — {f.explain_label}", item.why, 60)
            if why:
                f.why_default = f.why
                if f.kind in ("form_wording", "form_replaced") and f.why and "[[" in f.why:
                    f.why = f.why.split("]]. ")[0] + "]]. " + why      # keep the verbatim passage, replace the comment
                else:
                    f.why = why
                f.why_source = "model"
        an.narratives.sections[s] = section_from(res, s, rows, an, gate)


def section_from(res, s: str, rows: list[Finding], an: "Analysis", gate: TextGate,
                 allow_reduction_words: bool = False) -> SectionNarrative:
    """Model section text through the gate; rejected pieces fall back to the rule-based default. If the model's
    own summary contradicted the facts, its risk judgement is not trusted either."""
    from policy_compare.engine import narrative_context
    d = default_section(s, rows, {**narrative_context(an), "topics": [t.topic for t in an.focus]})
    before = len(an.audit.get("rejected_text", []))
    tag = f"§{SECTION_TITLES[s]}"
    # nothing here reduces cover: reject claims that it does (observations describe existing limits, so they may)
    nr = not allow_reduction_words and not any(r.impact == "reduced" for r in rows)
    # nothing material changed here: no "changes noted" / "topic affected" (observations are not changes at all)
    nc = s != "observations" and not any(r.changed and r.impact != "no_impact" for r in rows)
    bullets = [b for b in (gate.take(f"{tag} bullet", x, 18, kind="bullet", no_reduction=nr, no_change=nc)
                           for x in res.bullets[:3]) if b]
    n = SectionNarrative(
        headline=gate.take(f"{tag} headline", res.headline, 30, kind="headline", no_reduction=nr, no_change=nc) or d.headline,
        bullets=bullets or d.bullets,
        takeaway=gate.take(f"{tag} takeaway", res.takeaway, 25, kind="headline", no_reduction=nr, no_change=nc) or d.takeaway,
        risk_level=res.risk_level,
        risk_statement=gate.take(f"{tag} risk statement", res.risk_statement, 36, no_reduction=nr, no_change=nc)
        or d.risk_statement,
        confidence=res.confidence,
        confidence_reason=gate.take(f"{tag} confidence reason", res.confidence_reason, 36) or d.confidence_reason,
        source="model")
    contradicted = any(r["reason"].startswith("contradicts") for r in an.audit.get("rejected_text", [])[before:])
    if contradicted:
        n.risk_level, n.confidence = d.risk_level, d.confidence
        an.audit.setdefault("guards", []).append(f"section {s}: model text contradicted the facts; rule-based risk used")
    return n


def pick_topics(llm: LLMClient, an: "Analysis") -> list[str]:
    """Topics the client is likely to ask about: driven by their operations as well as by what changed."""
    changes = [{"label": f.label, "change": f.change, "impact": f.impact, "severity": f.severity,
                "title": f.context.get("title")} for f in an.all_findings() if f.changed][:40]
    exclusions = [f"{f.number} — {f.title}" for k, f in an.fr.items()
                  if k in an.fe and any(w in f.title.lower() for w in ("exclusion", "excluded", "limitation"))][:25]
    try:
        res = llm.structured("topics", prompt("topics").format(profile=_dump(client_profile(an)), changes=_dump(changes),
                                                                exclusions=_dump(exclusions)), TopicPick)
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
            page = ti_e.contains_verbatim(ch.quote, MIN_QUOTE_WORDS)
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
