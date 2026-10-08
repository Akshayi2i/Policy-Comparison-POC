"""Comparison pipeline: two canonical JSONs in, an Analysis (findings + narratives) out."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from policy_compare.analysis.narrative import (
    CHANGE_SECTIONS, SECTION_ORDER, Narratives, default_critical, default_exec, default_section,
)
from policy_compare.diff import Ids, compare_elements, expected_missing, policy_level
from policy_compare.findings import IMPACT_RANK, SEVERITY_RANK, Finding
from policy_compare.flatten import Element, flatten_pair
from policy_compare.fmt import mdy
from policy_compare.forms import Form, compare_forms
from policy_compare.ingest import Side, load_pair
from policy_compare.settings import config, settings
from policy_compare.textindex import TextIndex
from policy_compare.xref import FocusTopic, checklist, default_topics, focus_topics, midterm_findings, pending

log = logging.getLogger("policy_compare")


@dataclass
class Analysis:
    E: Side
    R: Side
    ti_e: TextIndex
    ti_r: TextIndex
    e_el: dict[str, Element]
    r_el: dict[str, Element]
    findings: dict[str, list[Finding]]
    forms_unchanged: list[Form]
    fe: dict[str, Form]
    fr: dict[str, Form]
    focus: list[FocusTopic]
    topics_supplied: bool
    narratives: Narratives = field(default_factory=Narratives)
    warnings: list[str] = field(default_factory=list)
    audit: dict = field(default_factory=dict)
    drafts: Optional[object] = None          # ReportDrafts
    recommendations: list = field(default_factory=list)   # analysis.recommend.Rec

    @property
    def deadline(self) -> str:
        return mdy(self.E.expiration)

    def all_findings(self) -> list[Finding]:
        return [f for s in SECTION_ORDER for f in self.findings.get(s, [])]

    def unique_changes(self) -> list[Finding]:
        """One finding per change_key (the most severe), across all sections — the executive 'changes found'."""
        best: dict[str, Finding] = {}
        for f in (f for s in CHANGE_SECTIONS for f in self.findings.get(s, [])):   # observations are not changes
            if not f.changed:
                continue
            cur = best.get(f.change_key)
            if cur is None or (IMPACT_RANK[f.impact], SEVERITY_RANK[f.severity]) < (IMPACT_RANK[cur.impact], SEVERITY_RANK[cur.severity]):
                best[f.change_key] = f
        return list(best.values())

    def critical_candidates(self) -> list[Finding]:
        cfg = config("rubric")["critical_changes"]
        cands = [f for f in self.unique_changes() if f.impact == "reduced" and f.severity in cfg["severities"]]
        return sorted(cands, key=lambda f: SEVERITY_RANK[f.severity])

    def premium(self) -> Optional[tuple[float, float]]:
        for f in self.findings.get("premium", []):
            el = self.e_el.get(f.element_key or "") if f.element_key else None
            if el and el.role == "total_premium":
                er = self.r_el.get(f.element_key)
                if el.cell.amount is not None and er and er.cell.amount is not None:
                    return el.cell.amount, er.cell.amount
        return None


def analyse(a: str | Path | dict, b: str | Path | dict, focus: Optional[list[str]] = None, use_llm: Optional[bool] = None,
            pdfs: Optional[list] = None) -> Analysis:
    """pdfs: optional original policy PDFs, used only to read scanned pages with a vision model."""
    t0 = time.time()
    E, R, warnings = load_pair(a, b)
    ids = Ids()
    llm = None
    want_llm = settings().llm_enabled if use_llm is None else (use_llm and settings().llm_enabled)
    if use_llm and not settings().llm_enabled:
        warnings.append("LLM requested but LLM_BASE_URL is not set; narrative uses deterministic defaults")
    if want_llm:
        from policy_compare.llm.client import LLMClient
        llm = LLMClient()
    ocr = {}
    if llm and settings().vision_enabled:            # F: scanned pages become text before anything reads them
        from policy_compare.ocr import run_ocr
        ocr = run_ocr(llm, [E, R], pdfs or [], warnings)
    ti_e, ti_r = TextIndex(E), TextIndex(R)

    e_el, r_el, unmatched, unaligned = flatten_pair(E.data, R.data)
    if unaligned:
        warnings.append(f"lists compared by position (no identity key): {', '.join(unaligned)}")
    if unmatched and llm:
        from policy_compare.analysis.assess import classify_unmatched
        classify_unmatched(llm, unmatched, e_el, r_el)

    midterm = midterm_findings(E, ti_e, ids)
    if midterm and llm:
        from policy_compare.analysis.assess import extract_midterm
        midterm = extract_midterm(llm, E, R, ti_e, e_el, r_el, midterm, ids)

    values = policy_level(E, R, ti_e, ti_r, e_el, r_el, ids) + compare_elements(e_el, r_el, E, R, ti_e, ti_r, ids)
    form_findings, unchanged, fe, fr = compare_forms(E, R, ti_e, ti_r, ids)

    by_section: dict[str, list[Finding]] = {s: [] for s in SECTION_ORDER}
    for f in values + form_findings + midterm:
        by_section[f.section].append(f)

    supplied = bool(focus)
    topics = [t.strip() for t in (focus or []) if t.strip()]
    an = Analysis(E=E, R=R, ti_e=ti_e, ti_r=ti_r, e_el=e_el, r_el=r_el, findings=by_section, forms_unchanged=unchanged,
                  fe=fe, fr=fr, focus=[], topics_supplied=supplied, warnings=warnings)

    # model: classify / explain section rows before the cross-reference sections are derived from them
    evidence: dict = {}
    if llm:
        from policy_compare.analysis.equivalence import match_equivalents
        from policy_compare.analysis.formroles import classify_forms
        classify_forms(llm, an)                       # what each form does, read from its text
        match_equivalents(llm, an)                    # B: removed + added forms that are one replacement
        from policy_compare.analysis.assess import assess_sections
        from policy_compare.analysis.slots import assess_slots
        assess_sections(llm, an)
        evidence = assess_slots(llm, an)              # C: contract requirements from the form wording
    if not topics:
        if llm:
            from policy_compare.analysis.assess import pick_topics
            topics = pick_topics(llm, an)
        topics = topics or default_topics(an.all_findings())
    base = [f for s in ("policy", "premium", "limits", "terms", "forms", "midterm") for f in by_section[s]]
    by_section["checklist"] = checklist(E, R, fe, fr, base, by_section["midterm"], ti_e, ti_r, ids, evidence=evidence)
    an.focus = focus_topics(topics, base, ti_e, ti_r)
    by_section["focus"] = [_focus_finding(t, ids) for t in an.focus]
    by_section["pending"] = pending(base, ids)
    if llm:                                           # A: coverage observations (unchanged terms that matter)
        from policy_compare.analysis.observe import observe
        by_section["observations"] = observe(llm, an, ids)

    # narratives: defaults first, the model overrides validated pieces
    ctx = narrative_context(an)
    for s in SECTION_ORDER:
        if s not in an.narratives.sections:          # model text (if any) already set for the value sections
            an.narratives.sections[s] = default_section(s, by_section[s], {**ctx, "topics": topics,
                                                                           "observations_ran": an.audit.get("observations_ran", False)})
    crit = an.critical_candidates()[: config("rubric")["critical_changes"]["max_items"]]
    for f in crit:
        an.narratives.critical[f.id] = default_critical(f, an.deadline)
    an.narratives.critical_order = [f.id for f in crit]
    an.narratives.executive = default_exec(an.narratives.sections, an.unique_changes(), crit, an.premium(), an.deadline)
    if llm:
        from policy_compare.analysis.judge import judge
        from policy_compare.analysis.synthesize import synthesize
        synthesize(llm, an, ctx)
    from policy_compare.analysis.recommend import recommend
    an.recommendations = recommend(llm, an)            # recommended actions (rule-based without the model)
    if llm:
        if settings().llm_judge:
            judge(llm, an)                            # G: second pass over the model's own statements
    from policy_compare.analysis.guards import apply_guards
    apply_guards(an)
    from policy_compare.analysis.drafts import build_drafts
    an.drafts = build_drafts(an, llm)                 # D: carrier email + client letter

    an.audit.update({
        "expiring": E.file_name, "renewal": R.file_name, "llm": bool(llm),
        "model": settings().llm_model if llm else None, "elapsed_s": round(time.time() - t0, 1),
        "unmatched_paths": [u.pattern for u in unmatched], "topics": topics,
        "llm_calls": llm.calls if llm else [], "warnings": warnings,
        "ocr_pages": ocr, "vision": bool(llm and settings().vision_enabled),
    })
    return an


def _focus_finding(t: FocusTopic, ids: Ids) -> Finding:
    from policy_compare.xref import most_severe
    worst = most_severe(t.related)
    f = Finding(id=ids.next(), change_key=worst.change_key if worst else f"focus:{t.topic}", section="focus", kind="focus",
                label=t.topic, exp=f"{t.mentions_e}", ren=f"{t.mentions_r}",
                duplicate_of=worst.change_key if worst else None)
    if worst:
        f.impact, f.severity, f.locked = worst.impact, worst.severity, True
        f.change = f"{worst.label}: {worst.change.lower()}"
    elif t.mentions_e != t.mentions_r:
        f.impact, f.severity, f.locked = "confirm", "low", False
        f.change_key = f"focus:{t.topic}"
        f.change = f"mentions {t.mentions_e} → {t.mentions_r}"
    return f


def narrative_context(an: Analysis) -> dict:
    def rng(side: Side, ti: TextIndex) -> str:
        p = ti.declarations_pages
        return f"{side.ref} p.{p[0]}–{p[-1]}" if len(p) > 1 else (f"{side.ref} p.{p[0]}" if p else "")
    decl = ", ".join(x for x in (rng(an.E, an.ti_e), rng(an.R, an.ti_r)) if x)
    slot_forms = [f for f in list(an.fe.values()) + list(an.fr.values()) if f.slots]
    iso = {s for slot in config("contract_slots").get("slots", []) for s in slot.get("forms", [])}
    from policy_compare.textindex import form_key
    by_title = any(f.key not in {form_key(x) for x in iso} for f in slot_forms)
    return {"decl_pages": decl, "scanned": len(an.ti_e.scanned_pages()) + len(an.ti_r.scanned_pages()),
            "slots_by_title": by_title, "deadline": an.deadline}
