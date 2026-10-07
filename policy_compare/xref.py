"""Cross-reference sections: mid-term changes (8), contract checklist (9), client focus (10), pending items (11)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from policy_compare.diff import Ids, why
from policy_compare.findings import IMPACT_RANK, SEVERITY_RANK, Finding
from policy_compare.forms import Form
from policy_compare.ingest import Document, Side
from policy_compare.settings import config
from policy_compare.textindex import Sentence, TextIndex, norm_key, norm_space

CONTRACT_WHY = "Contracts that require it would be breached, and certificates issued on expiring terms would overstate coverage."


def _ref(side: Side, page: Optional[int]) -> Optional[str]:
    return f"{side.ref} p.{page}" if page else None


def most_severe(fs: list[Finding]) -> Optional[Finding]:
    fs = [f for f in fs if f.changed]
    return min(fs, key=lambda f: (IMPACT_RANK.get(f.impact, 9), SEVERITY_RANK.get(f.severity, 9))) if fs else None


# ---------------- Section 8: mid-term changes ----------------

def midterm_documents(side: Side) -> list[Document]:
    """Documents that look like policy-change endorsements (by title/bookmark only, to avoid body-text noise)."""
    phrases = config("catalogue").get("signals", {}).get("midterm_change_titles", [])
    out = []
    for doc in side.model.documents:
        title = f"{doc.title or ''} {doc.bookmark_title or ''}".lower()
        if any(p in title for p in phrases):
            out.append(doc)
    return out


def midterm_findings(E: Side, ti_e: TextIndex, ids: Ids) -> list[Finding]:
    """Without model extraction each detected change document is a 'review' row; the model refines these."""
    out = []
    for doc in midterm_documents(E):
        out.append(Finding(id=ids.next(), change_key=f"midterm:{doc.form_number or doc.title}", section="midterm", kind="midterm_doc",
                           label=f"Policy change · {doc.title or doc.form_number}", exp=doc.form_number or "Present",
                           exp_ref=_ref(E, doc.page_start), ren="", change="Policy change document found - review",
                           impact="confirm", severity="low", locked=False,
                           why="A policy-change document is attached to the expiring policy; confirm whether its changes were carried into the renewal.",
                           context={"document_excerpt": norm_space(ti_e.document_text(doc))[:1500]}))
    return out


# ---------------- Section 9: contract requirements & broker checklist ----------------

def checklist(E: Side, R: Side, fe: dict[str, Form], fr: dict[str, Form], findings: list[Finding], midterm: list[Finding],
              ti_e: TextIndex, ti_r: TextIndex, ids: Ids) -> list[Finding]:
    cfg = config("contract_slots")
    by_key = {f.change_key: f for f in findings}
    out: list[Finding] = []
    for slot in cfg.get("slots", []):
        se = [f for f in fe.values() if slot["id"] in f.slots]
        sr = [f for f in fr.values() if slot["id"] in f.slots]
        label = f"Contract requirement: {slot['label']}"
        exp = "Yes - " + ", ".join(f.number for f in se) if se else "Not found"
        ren = "Yes - " + ", ".join(f.number for f in sr) if sr else "Not found"
        f = Finding(id=ids.next(), change_key=f"slot:{slot['id']}", section="checklist", kind="contract_slot", label=label,
                    exp=exp, ren=ren, exp_ref=_ref(E, se[0].page) if se else None, ren_ref=_ref(R, sr[0].page) if sr else None,
                    context={"slot": slot["id"], "expiring_forms": [x.full_title for x in se], "renewal_forms": [x.full_title for x in sr]})
        if se and not sr:
            f.impact, f.severity, f.locked, f.change, f.why = "reduced", "critical", True, "Lost on renewal", CONTRACT_WHY
            f.duplicate_of = f"form:{se[0].key}" if f"form:{se[0].key}" in by_key else None
        elif sr and not se:
            f.impact, f.severity, f.locked, f.change = "improved", "medium", True, "Added on renewal"
            f.why = "The renewal now meets this common contract requirement."
            f.duplicate_of = f"form:{sr[0].key}" if f"form:{sr[0].key}" in by_key else None
        elif not se and not sr:
            f.change = "Not on either policy"
        else:
            f.change = "No change"
        if f.duplicate_of:
            f.change_key = f.duplicate_of
        out.append(f)

    # broker checks
    sig = config("catalogue").get("signals", {})
    retro_e, retro_r = _retro(ti_e), _retro(ti_r)
    cm_e = bool(ti_e.search_any(sig.get("claims_made", []))) and bool(retro_e)
    cm_r = bool(ti_r.search_any(sig.get("claims_made", []))) and bool(retro_r)
    f = Finding(id=ids.next(), change_key="check:claims_made", section="checklist", kind="broker_check",
                label="Claims-made continuity (retroactive date)",
                exp=f"Claims-Made; retro {retro_e[1]}" if cm_e else "No retroactive date found",
                ren=f"Claims-Made; retro {retro_r[1]}" if cm_r else "No retroactive date found",
                exp_ref=_ref(E, retro_e[0]) if cm_e else None, ren_ref=_ref(R, retro_r[0]) if cm_r else None)
    if cm_e and cm_r and retro_e[1] != retro_r[1]:
        f.impact, f.severity, f.locked, f.change = "reduced", "critical", True, "Retro advanced"
        f.why = "Loss of prior acts - incidents before the new retroactive date are no longer covered."
    elif cm_e and not cm_r:
        f.impact, f.severity, f.locked, f.change = "confirm", "high", False, "Retro date not found on renewal"
        f.why = "The expiring policy is claims-made; confirm the renewal keeps the retroactive date."
    else:
        f.change = "No change" if cm_e else "Not applicable (no claims-made retro date)"
    out.append(f)

    mt_changed = [m for m in midterm if m.changed]
    f = Finding(id=ids.next(), change_key="check:midterm", section="checklist", kind="broker_check",
                label="Mid-term changes carried into renewal",
                exp=f"{len(mt_changed)} change{'s' if len(mt_changed) != 1 else ''} mid-term" if midterm else "None found",
                ren="—" if not midterm else f"{sum(1 for m in mt_changed if m.impact == 'reduced')} not carried forward")
    if any(m.impact == "reduced" for m in mt_changed):
        worst = most_severe(mt_changed)
        f.impact, f.severity, f.locked, f.change = worst.impact, worst.severity, True, worst.label
        f.why, f.change_key = "See Section 8.", worst.change_key
        f.duplicate_of = worst.change_key
    elif mt_changed:
        f.impact, f.severity, f.locked, f.change, f.why = "confirm", "low", False, "Review", "See Section 8."
    else:
        f.change = "No mid-term changes found"
    out.append(f)

    ins = next((x for x in findings if x.element_key == "carrier.name"), None)
    f = Finding(id=ids.next(), change_key="check:carrier", section="checklist", kind="broker_check", label="Carrier change",
                exp=E.carrier or "—", ren=R.carrier or "—", exp_ref=ins.exp_ref if ins else None, ren_ref=ins.ren_ref if ins else None)
    if ins and ins.changed:
        f.impact, f.severity, f.locked, f.change = ins.impact, ins.severity, True, "New carrier"
        f.why = "Confirm AM Best rating and admitted status, reissue certificates, check lender/landlord notice requirements."
        f.change_key = f.duplicate_of = ins.change_key
    else:
        f.change = "No change"
    out.append(f)

    notes_e, notes_r = _wording_notes(E, fe), _wording_notes(R, fr)
    wording = [x for x in findings if x.kind in ("form_wording", "form_edition")]
    f = Finding(id=ids.next(), change_key="check:wording_notes", section="checklist", kind="broker_check",
                label="Endorsement wording that needs a read",
                exp=f"{len(notes_e)} note{'s' if len(notes_e) != 1 else ''}" if notes_e else "None",
                ren=f"{len(notes_r)} note{'s' if len(notes_r) != 1 else ''}" if notes_r else "None")
    notes = [f"[expiring] {n}" for n in notes_e] + [f"[renewal] {n}" for n in notes_r] + \
            [f"{w.label}: {w.change}" for w in wording]
    if notes:
        f.impact, f.severity, f.locked = "confirm", "low", False
        f.change = f"{len(notes)} note{'s' if len(notes) != 1 else ''}"
        f.why = " • ".join(notes)
    else:
        f.change = "Nothing flagged"
    out.append(f)

    for chk in cfg.get("broker_checks", []):
        if chk.get("out_of_scope"):
            out.append(Finding(id=ids.next(), change_key=f"check:{chk['id']}", section="checklist", kind="broker_check",
                               label=chk["label"], exp="—", ren="—", change="Out of scope in comparison mode"))
    # changed rows first (by severity), then unchanged, then out-of-scope (kept last)
    oos = [x for x in out if x.change == "Out of scope in comparison mode"]
    rest = [x for x in out if x not in oos]
    rest.sort(key=lambda x: (0 if x.changed else 1, IMPACT_RANK.get(x.impact or "", 9), SEVERITY_RANK.get(x.severity or "", 9)))
    return rest + oos


def _retro(ti: TextIndex) -> Optional[tuple[int, str]]:
    pat = re.compile(r"retro(?:active)?\s+date[:\s]*([0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4}|none|n/a)", re.I)
    for pg in [*ti.declarations_pages, *sorted(ti.pages)]:
        m = pat.search(ti.pages.get(pg, ""))
        if m:
            return pg, m.group(1)
    return None


def _wording_notes(side: Side, forms: dict[str, Form]) -> list[str]:
    notes = []
    for f in forms.values():
        if f.attached is False:
            notes.append(f"{f.number} {f.edition} is scheduled but not attached")
        if f.edition_matches is False:
            notes.append(f"{f.number} schedule edition differs from the attached form")
    return notes


# ---------------- Section 10: client focus areas ----------------

@dataclass
class FocusTopic:
    topic: str
    mentions_e: int
    mentions_r: int
    related: list[Finding] = field(default_factory=list)
    candidates_e: list[Sentence] = field(default_factory=list)
    candidates_r: list[Sentence] = field(default_factory=list)
    quotes: list[tuple[str, Sentence]] = field(default_factory=list)   # (side, sentence)


def default_topics(findings: list[Finding], n: int = 3) -> list[str]:
    """Offline fallback when no topics were supplied: the subjects of the most severe changes."""
    stop = {"exclusion", "endorsement", "amendment", "important", "notice", "form", "coverage", "limit", "policy",
            "the", "of", "and", "or", "to", "ny", "condition", "conditions", "removed", "added"}
    out: list[str] = []
    ranked = sorted([f for f in findings if f.changed and f.impact != "no_impact" and f.section in ("forms", "limits", "terms", "premium", "policy")],
                    key=lambda f: (IMPACT_RANK.get(f.impact, 9), SEVERITY_RANK.get(f.severity, 9)))
    for f in ranked:
        title = f.context.get("title") or f.label
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]+", title) if w.lower() not in stop]
        if words:
            t = " ".join(words[:2]).lower()
            if t not in out:
                out.append(t)
        if len(out) >= n:
            break
    return out


def focus_topics(topics: list[str], findings: list[Finding], ti_e: TextIndex, ti_r: TextIndex) -> list[FocusTopic]:
    out = []
    for t in topics:
        t = t.strip()
        if not t:
            continue
        words = [w for w in norm_key(t).split() if len(w) > 2]
        related = []
        for f in findings:
            if not f.changed or f.section in ("checklist", "pending"):
                continue
            hay = norm_key(" ".join([f.label, str(f.context.get("title", "")), str(f.context.get("expiring_passage", "")),
                                     str(f.context.get("renewal_passage", ""))]))
            if words and all(w in hay for w in words):
                related.append(f)
        pages_e = _related_pages(related, ti_e, "exp_ref")
        pages_r = _related_pages(related, ti_r, "ren_ref")
        ft = FocusTopic(topic=t, mentions_e=ti_e.count(t), mentions_r=ti_r.count(t), related=related,
                        candidates_e=ti_e.sentences_with(t, prefer_pages=pages_e),
                        candidates_r=ti_r.sentences_with(t, prefer_pages=pages_r))
        # default quotes: first sentence per side
        if ft.candidates_e:
            ft.quotes.append(("expiring", ft.candidates_e[0]))
        if ft.candidates_r:
            ft.quotes.append(("renewal", ft.candidates_r[0]))
        out.append(ft)
    return out


def _related_pages(related: list[Finding], ti: TextIndex, attr: str) -> list[int]:
    """Pages of the documents behind related changes (the form itself, not just its schedule line)."""
    pages: list[int] = []
    for f in related:
        num = f.context.get("form_number")
        doc = ti.document_for_form(num) if num else None
        if doc:
            pages += [p.page_number for p in doc.pages]
        ref = getattr(f, attr)
        if ref and " p." in ref:
            pages.append(int(ref.split(" p.")[1]))
    return pages


# ---------------- Section 11: items pending confirmation ----------------

def pending(findings: list[Finding], ids: Ids) -> list[Finding]:
    out = []
    for f in findings:
        if f.impact == "confirm" and f.kind in ("form_removed", "form_added") and not f.duplicate_of:
            out.append(Finding(id=ids.next(), change_key=f.change_key, section="pending", kind="pending", label=f.label,
                               sublabel=f.why if f.why_source == "model" else None, exp=f.exp, ren=f.ren if f.kind == "form_added" else "—",
                               exp_ref=f.exp_ref, ren_ref=f.ren_ref, change="Pending confirmation with the carrier",
                               impact=f.impact, severity=f.severity, locked=True, duplicate_of=f.change_key,
                               context={"source_finding": f.id}))
    return out
