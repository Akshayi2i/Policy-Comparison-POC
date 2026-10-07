"""B: cross-carrier form equivalence — a form removed at renewal may be replaced by a form with another number.

The model pairs removed forms with added forms that serve the same purpose and says whether the replacement is
equivalent, narrower or broader, quoting both wordings. A pair is kept only when both form numbers exist on the
right side, each form is used once, and both quotes are verbatim in that form's own pages. A kept pair turns the
"removed" and "added" rows into one "replaced by" row; anything else stays as removed + added.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Optional

from policy_compare.analysis.guards import TextGate
from policy_compare.findings import Finding
from policy_compare.llm.client import LLMClient, LLMError, prompt
from policy_compare.schema.llm_io import FormPairing
from policy_compare.textindex import MIN_QUOTE_WORDS, TextIndex, norm_space, quote_key

if TYPE_CHECKING:
    from policy_compare.engine import Analysis
    from policy_compare.forms import Form

MAX_FORMS = 12
EXCERPT_CHARS = 700
RELATION = {
    "equivalent": ("Replaced by an equivalent form", "no_impact", "low"),
    "narrower": ("Replaced by a narrower form", "reduced", "medium"),
    "broader": ("Replaced by a broader form", "improved", "low"),
}


def quote_page(ti: TextIndex, form: "Form", quote: Optional[str]) -> Optional[int]:
    """Page of a verbatim quote inside this form's own pages (None if the quote is not there)."""
    q = quote_key(quote or "")
    if not (q and form.doc and len(q.split()) >= MIN_QUOTE_WORDS):
        return None
    for p in form.doc.pages:
        if q in norm_space(ti.pages.get(p.page_number, p.text or "")):
            return p.page_number
    return None


def _items(rows: list[Finding], forms: dict, ti: TextIndex, prefix: str) -> list[dict]:
    out = []
    for i, f in enumerate(rows, 1):
        form = forms[f.change_key.split(":", 1)[1]]
        out.append({"id": f"{prefix}{i}", "form_number": form.number, "title": form.title,
                    "excerpt": norm_space(ti.document_text(form.doc))[:EXCERPT_CHARS] if form.doc else ""})
    return out


def match_equivalents(llm: LLMClient, an: "Analysis") -> None:
    rows = an.findings["forms"]
    removed = [f for f in rows if f.kind == "form_removed"][:MAX_FORMS]
    added = [f for f in rows if f.kind == "form_added"][:MAX_FORMS]
    if not (removed and added):
        return
    rem_items, add_items = _items(removed, an.fe, an.ti_e, "X"), _items(added, an.fr, an.ti_r, "N")
    user = prompt("equivalence").format(removed=json.dumps(rem_items, ensure_ascii=False, indent=1),
                                        added=json.dumps(add_items, ensure_ascii=False, indent=1))
    try:
        res = llm.structured("form_equivalence", user, FormPairing)
    except LLMError as e:
        an.warnings.append(f"form equivalence check failed; removed and added forms are listed separately ({e})")
        return

    rem_by = {it["id"]: (f, an.fe[f.change_key.split(":", 1)[1]]) for it, f in zip(rem_items, removed)}
    add_by = {it["id"]: (f, an.fr[f.change_key.split(":", 1)[1]]) for it, f in zip(add_items, added)}
    gate, used, log = TextGate(an), set(), an.audit.setdefault("form_equivalence", [])
    for p in res.pairs:
        entry = {"removed": p.removed_id, "added": p.added_id, "relation": p.relation}
        if p.removed_id not in rem_by or p.added_id not in add_by:
            log.append({**entry, "kept": False, "reason": "unknown id"})
            continue
        if p.removed_id in used or p.added_id in used:
            log.append({**entry, "kept": False, "reason": "form already paired"})
            continue
        (fe_row, old), (fr_row, new) = rem_by[p.removed_id], add_by[p.added_id]
        entry.update({"removed": old.number, "added": new.number})
        pe, pr = quote_page(an.ti_e, old, p.expiring_quote), quote_page(an.ti_r, new, p.renewal_quote)
        if not (pe and pr):
            log.append({**entry, "kept": False, "reason": "quote not verbatim in the form text"})
            continue
        reason = gate.take(f"§7 {old.number} → {new.number} replacement", p.reason, 40)
        if not reason:
            log.append({**entry, "kept": False, "reason": "explanation rejected by the gate"})
            continue
        used |= {p.removed_id, p.added_id}
        _merge(an, fe_row, fr_row, old, new, p.relation, reason, p.expiring_quote, p.renewal_quote, pe, pr)
        log.append({**entry, "kept": True})


def _merge(an: "Analysis", fe_row: Finding, fr_row: Finding, old: "Form", new: "Form", relation: str, reason: str,
           q_old: str, q_new: str, pe: int, pr: int) -> None:
    change, impact, severity = RELATION[relation]
    lost_slots = [s for s in old.slots if s not in new.slots]
    q_old, q_new = q_old.strip().strip("“”\""), q_new.strip().strip("“”\"")
    f = fe_row
    f.kind, f.group, f.change = "form_replaced", "replaced", change
    f.sublabel = f"Replaced by {new.full_title}"
    f.ren, f.ren_ref = new.display, fr_row.ren_ref
    if f.locked and lost_slots:                # a lost contract requirement stays a rubric-locked reduction
        f.change = f"{change}; contract requirement not matched on the replacement"
    else:
        f.impact, f.severity, f.locked = impact, severity, True
    evidence = f"expiring “{q_old}” [[{an.E.ref} p.{pe}]] → renewal “{q_new}” [[{an.R.ref} p.{pr}]]."
    f.why_default = f"{evidence} Compare the two forms before presenting."
    f.why = f"{evidence} {reason}"
    f.why_source, f.explain_label = "model", "Replacement"
    f.context.update({"replaced_by": new.full_title, "relation": relation, "renewal_form_number": new.number,
                      "renewal_form_role": fr_row.context.get("form_role"), "expiring_passage": q_old,
                      "renewal_passage": q_new, "contract_slots_lost": lost_slots,
                      "explain_no_impact": True})            # an equivalent replacement still shows its evidence
    f.context.pop("related_forms_still_on_renewal", None)
    an.findings["forms"].remove(fr_row)
