"""C: contract requirements read from the form wording (Section 9).

For each requirement slot the model reads the passages of each policy's forms that mention it and decides whether the
requirement is met, partly met or not found. A 'met'/'partial' verdict only counts when its quote is verbatim in the
named form; otherwise the title-based match stays.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from policy_compare.llm.client import LLMClient, LLMError, prompt
from policy_compare.schema.llm_io import SlotAssessment
from policy_compare.settings import config
from policy_compare.textindex import MIN_QUOTE_WORDS, form_key, norm_space

if TYPE_CHECKING:
    from policy_compare.engine import Analysis
    from policy_compare.ingest import Side
    from policy_compare.textindex import TextIndex

WINDOW = 260
MAX_FORMS_PER_SLOT = 4


@dataclass
class SlotEvidence:
    status: str                 # met | partial | not_found
    form_number: Optional[str]
    quote: Optional[str]
    page: Optional[int]
    note: str


def _passages(text: str, words: list[str]) -> list[str]:
    flat = norm_space(text)
    low = flat.lower()
    out = []
    for w in words:
        i = low.find(w.lower())
        if i >= 0:
            out.append(flat[max(0, i - WINDOW): i + len(w) + WINDOW])
        if len(out) >= 2:
            break
    return out


def candidates(forms: dict, ti: "TextIndex") -> dict[str, list[dict]]:
    """slot id -> forms on this policy whose text mentions the slot, with the passages around the mentions."""
    out: dict[str, list[dict]] = {}
    for slot in config("contract_slots").get("slots", []):
        words = slot.get("text_any", [])
        hits = []
        for f in forms.values():
            if not f.doc:
                continue
            ps = _passages(ti.document_text(f.doc), words)
            if ps or slot["id"] in f.slots:
                hits.append({"form_number": f.number, "title": f.title, "passages": ps})
        out[slot["id"]] = hits[:MAX_FORMS_PER_SLOT]
    return out


def _ask(llm: LLMClient, cands: dict, an: "Analysis", side: str) -> dict[str, SlotEvidence]:
    from policy_compare.analysis.assess import client_profile
    slots = [{"slot": s["id"], "requirement": s["label"], "candidate_forms": cands.get(s["id"], [])}
             for s in config("contract_slots").get("slots", [])]
    user = prompt("slots").format(profile=json.dumps(client_profile(an), ensure_ascii=False, indent=1),
                                  slots=json.dumps(slots, ensure_ascii=False, indent=1))
    res = llm.structured(f"slots_{side}", user, SlotAssessment)
    return {v.slot: v for v in res.verdicts}


def verify(verdicts: dict, forms: dict, ti: "TextIndex", an: "Analysis", side: str) -> dict[str, SlotEvidence]:
    """Keep only verdicts whose form exists on this policy and whose quote is verbatim in that form."""
    out: dict[str, SlotEvidence] = {}
    by_key = {form_key(f.number): f for f in forms.values()}
    rejected = an.audit.setdefault("rejected_text", [])
    for slot, v in verdicts.items():
        if v.status == "not_found":
            out[slot] = SlotEvidence("not_found", None, None, None, v.note)
            continue
        f = by_key.get(form_key(v.form_number or ""))
        page = ti.contains_verbatim(v.quote or "", MIN_QUOTE_WORDS) if f and f.doc else None
        if not f or not page or not any(p.page_number == page for p in f.doc.pages):
            rejected.append({"field": f"{side} contract slot {slot}", "text": v.quote, "reason": "form or verbatim quote not found"})
            continue
        out[slot] = SlotEvidence(v.status, f.number, (v.quote or "").strip().strip("“”\""), page, v.note)
    return out


def assess_slots(llm: LLMClient, an: "Analysis") -> dict[str, dict[str, SlotEvidence]]:
    """Returns {'expiring': {slot: evidence}, 'renewal': {...}} and updates each form's slot list."""
    ce, cr = candidates(an.fe, an.ti_e), candidates(an.fr, an.ti_r)
    evidence: dict[str, dict[str, SlotEvidence]] = {}
    try:
        ve = _ask(llm, ce, an, "expiring")
        vr = ve if json.dumps(ce, sort_keys=True) == json.dumps(cr, sort_keys=True) else _ask(llm, cr, an, "renewal")
    except LLMError as e:
        an.warnings.append(f"contract requirement reading failed; title matching used ({e})")
        return {}
    evidence["expiring"] = verify(ve, an.fe, an.ti_e, an, "expiring")
    evidence["renewal"] = verify(vr, an.fr, an.ti_r, an, "renewal")
    # a verified 'met'/'partial' verdict adds the slot to that form (title matches stay as they are)
    for side, forms in (("expiring", an.fe), ("renewal", an.fr)):
        by_key = {form_key(f.number): f for f in forms.values()}
        for slot, ev in evidence[side].items():
            if ev.status in ("met", "partial") and ev.form_number:
                f = by_key[form_key(ev.form_number)]
                if slot not in f.slots:
                    f.slots.append(slot)
    an.audit["contract_slots"] = {side: {k: vars(v) for k, v in ev.items()} for side, ev in evidence.items()}
    return evidence


def label_for(ev: Optional[SlotEvidence], forms_with_slot: list) -> str:
    """'Yes - BAI 1' when the wording confirms it; '(by title)' when only the title was matched."""
    if ev and ev.status in ("met", "partial") and ev.form_number:
        return f"{'Yes' if ev.status == 'met' else 'Partly'} - {ev.form_number}"
    if forms_with_slot:
        nums = ", ".join(f.number for f in forms_with_slot)
        return f"Partly - {nums} (by title)" if ev and ev.status == "not_found" else f"Yes - {nums} (by title)"
    return "Not found"


def quote_note(ev: Optional[SlotEvidence], ref: str) -> Optional[str]:
    if ev and ev.quote and ev.page:
        q = ev.quote if len(ev.quote) <= 160 else ev.quote[:159].rsplit(" ", 1)[0] + "…"
        return f"From the form wording: “{q}” [[{ref} p.{ev.page}]]"
    return None
