"""Assemble the ReportModel from an Analysis. All counts, KPIs and values are computed here."""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from policy_compare.analysis.narrative import SECTION_ORDER, SECTION_TITLES
from policy_compare.diff import expected_status
from policy_compare.engine import Analysis
from policy_compare.findings import IMPACT_RANK, SEVERITY_RANK, Finding
from policy_compare.fmt import long_date, mdy, money, money_compact, pct, plural
from policy_compare.schema.report import (
    Action, Badge, Cover, CriticalChange, CriticalSection, ExecutiveSummary, FocusBlock, FocusCard, Improvement, Kpi, Meta, Mix,
    NoteBlock, OverviewRow, PartyCard, QuoteLine, Report, RiskAnalysis, Row, Section, SectionSummary, SourceDoc, TableBlock,
    ThemeCount, THEME_ORDER,
)
from policy_compare.settings import config

COLUMNS = {
    "policy": ["Element", "Expiring", "Renewal", "Change / Assessment"],
    "premium": ["Element", "Expiring", "Renewal", "Change / Assessment"],
    "limits": ["Limit", "Expiring", "Renewal", "Change / Assessment"],
    "terms": ["Term", "Expiring", "Renewal", "Change / Assessment"],
    "forms": ["Form", "Expiring", "Renewal", "Effect"],
    "midterm": ["Step", "Expiring value", "Renewal", "Result"],
    "checklist": ["Check", "Expiring", "Renewal", "Change / Assessment"],
    "pending": ["Item", "Expiring", "Renewal", "Next step"],
}
FORM_GROUPS = [("replaced", "Forms replaced", "different form number, same purpose"),
               ("removed", "Forms removed", "on expiring, not on renewal"), ("added", "Forms added", "new on renewal"),
               ("edition", "Edition changes", None), ("wording", "Wording changes", "same form, different text")]
MAX_UNCHANGED_LIST = 14


def chip(text: str, ref: Optional[str]) -> str:
    return f"{text} [[{ref}]]" if ref and text not in ("—", "") else text


def badge(f: Finding) -> Optional[Badge]:
    return Badge(impact=f.impact, severity=f.severity) if f.changed else None


def row(f: Finding) -> Row:
    return Row(label=f.label, tag=f.tag, sublabel=f.sublabel, expiring=chip(f.exp, f.exp_ref), renewal=chip(f.ren, f.ren_ref),
               change=f.change, badge=badge(f), explain_label=f.explain_label,
               explain=f.why if (f.changed and f.impact != "no_impact" or f.context.get("explain_no_impact")) else None)


def mix_of(rows: list[Finding]) -> Mix:
    m = Mix()
    for f in rows:
        if f.changed:
            m.add(f.impact)
    return m


def _order(rows: list[Finding]) -> list[Finding]:
    return sorted(rows, key=lambda f: (0 if f.changed else 1, IMPACT_RANK.get(f.impact or "", 9), SEVERITY_RANK.get(f.severity or "", 9)))


def _value_blocks(an: Analysis, section: str) -> list:
    rows = an.findings[section]
    visible = [f for f in rows if f.changed or f.always_show]
    hidden = [f for f in rows if not f.changed and not f.always_show]
    groups: dict[str, list[Finding]] = {}
    for f in visible:
        groups.setdefault(f.group or "", []).append(f)
    blocks: list = []
    headed = len(groups) > 1
    if section == "policy":   # identity rows first, as in the reference
        ordered = [f for f in visible if f.element_key == "named_insured.name"] + \
                  _order([f for f in visible if f.element_key != "named_insured.name" and f.changed]) + \
                  [f for f in visible if f.element_key != "named_insured.name" and not f.changed]
        groups = {"": ordered}
        headed = False
    for g, fs in groups.items():
        fs = fs if section == "policy" else _order(fs)
        blocks.append(TableBlock(heading=g if headed else None, columns=COLUMNS[section], rows=[row(f) for f in fs]))
    notes = []
    if hidden:
        names = [re.sub(r"\s+—\s+(limit|value|amount)$", "", f.label) for f in hidden]
        names = list(dict.fromkeys(names))
        shown = ", ".join(names[:MAX_UNCHANGED_LIST]) + (f" and {len(names) - MAX_UNCHANGED_LIST} more" if len(names) > MAX_UNCHANGED_LIST else "")
        notes.append(f"**Also unchanged:** {shown}.")
    missing, via_form = expected_status(section, an)
    if missing:
        notes.append(f"Not shown on either declarations page (may be set by the policy forms): {', '.join(missing)}.")
    if via_form:
        notes.append(f"Not on the declarations but provided by form: {'; '.join(via_form)}.")
    if not blocks and not notes:
        blocks.append(NoteBlock(text="No values in this section were found on either policy."))
    if notes:
        last = blocks[-1] if blocks else None
        if isinstance(last, TableBlock):
            last.footnote = "  ".join(notes) if not last.footnote else last.footnote + "  " + "  ".join(notes)
        else:
            blocks.append(NoteBlock(text="  ".join(notes)))
    return blocks


def _actions(an: Analysis) -> list[Action]:
    from policy_compare.analysis.recommend import refs_for
    out = []
    for r in an.recommendations:
        chips = "".join(f" [[{ref}]]" for ref in refs_for(an, r))
        out.append(Action(priority=r.priority, audience=r.audience, action=r.action, reason=r.reason + chips))
    return out


def _forms_blocks(an: Analysis) -> list:
    rows = an.findings["forms"]
    blocks: list = []
    for key, heading, note in FORM_GROUPS:
        fs = _order([f for f in rows if f.group == key])
        if fs:
            blocks.append(TableBlock(variant="forms", heading=heading, heading_note=note, columns=COLUMNS["forms"], rows=[row(f) for f in fs]))
    if an.forms_unchanged:
        listing = ", ".join(chip(f.display, f"{an.R.ref} p.{f.page}" if f.page else None) for f in an.forms_unchanged)
        foot = f"**Forms unchanged:** {listing}."
        if blocks:
            blocks[-1].footnote = foot
        else:
            blocks.append(NoteBlock(text="No forms were added, removed or changed. " + foot))
    if not blocks:
        blocks.append(NoteBlock(text="No forms schedule was found on either policy."))
    return blocks


def _midterm_blocks(an: Analysis) -> list:
    rows = an.findings["midterm"]
    if not rows:
        return [NoteBlock(text="No mid-term changes or policy-change endorsements were found in the expiring policy documents; "
                               "the expiring column is the policy as issued.")]
    return [TableBlock(intro="The expiring column in this report is the policy as amended by these changes, applied in effective-date order.",
                       columns=COLUMNS["midterm"], rows=[row(f) for f in rows])]


def _focus_blocks(an: Analysis) -> list:
    if not an.focus:
        return [NoteBlock(text="No client focus topics were supplied. Pass --focus \"topic, topic\" to add them.")]
    cards = []
    quotes_sel = an.narratives.focus_quotes
    for t, f in zip(an.focus, an.findings["focus"]):
        rel = None
        if t.related:
            parts = [f"{r.label} ({r.change.lower()})" for r in t.related[:4]]
            rel = ("Related change: " if len(parts) == 1 else "Related changes: ") + "; ".join(parts) + "."
        mentions = f"{plural(t.mentions_e, 'mention')} in prior · {t.mentions_r} in renewal"
        chosen = quotes_sel.get(t.topic)
        qs = []
        if chosen:
            for side, idx in chosen:
                cands = t.candidates_e if side == "expiring" else t.candidates_r
                if 0 <= idx < len(cands):
                    qs.append((side, cands[idx]))
        else:
            qs = t.quotes
        lines = [QuoteLine(side=side, text=_trim_quote(s.text), ref=f"{(an.E if side == 'expiring' else an.R).ref} p.{s.page}") for side, s in qs]
        cards.append(FocusCard(topic=t.topic, badge=badge(f), mentions=mentions, related=rel, quotes=lines))
    return [FocusBlock(cards=cards)]


def _trim_quote(s: str, n: int = 150) -> str:
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def _pending_blocks(an: Analysis) -> list:
    rows = an.findings["pending"]
    if not rows:
        return [NoteBlock(text="No items are waiting on the carrier.")]
    return [TableBlock(variant="pending", columns=COLUMNS["pending"], rows=[row(f) for f in rows])]


def _checklist_blocks(an: Analysis) -> list:
    return [TableBlock(columns=COLUMNS["checklist"], rows=[row(f) for f in an.findings["checklist"]])]


def _observation_blocks(an: Analysis) -> list:
    rows = an.findings.get("observations", [])
    if not rows:
        ran = an.audit.get("observations_ran")
        return [NoteBlock(text="No exclusions or limitations on both policies were flagged for this client." if ran else
                          "Coverage observations are written by the model, which was not used for this report.")]
    return [TableBlock(intro="These terms did not change at renewal; they are listed because they matter for this client's "
                             "operations. Each is quoted from the renewal form.",
                       columns=["Observation", "Expiring", "Renewal", "Recommendation"], rows=[row(f) for f in rows])]


BUILDERS = {
    "policy": lambda an: _value_blocks(an, "policy"), "premium": lambda an: _value_blocks(an, "premium"),
    "limits": lambda an: _value_blocks(an, "limits"), "terms": lambda an: _value_blocks(an, "terms"),
    "forms": _forms_blocks, "midterm": _midterm_blocks, "checklist": _checklist_blocks,
    "focus": _focus_blocks, "pending": _pending_blocks, "observations": _observation_blocks,
}


def build_report(an: Analysis, prepared_on: Optional[date] = None) -> Report:
    brand = config("branding")
    E, R = an.E, an.R
    insured = R.insured or E.insured
    sections: list[Section] = []
    for s in SECTION_ORDER:
        rows = an.findings[s]
        n = an.narratives.sections[s]
        sections.append(Section(
            number=str(SECTION_ORDER.index(s) + 3), title=SECTION_TITLES[s], page_break_before=(s == "policy"),
            compact=not any(f.changed for f in rows) and n.risk_level == "low",
            summary=SectionSummary(headline=n.headline, bullets=n.bullets, mix=mix_of(rows), items_compared=sum(f.changed for f in rows)),
            risk=RiskAnalysis(level=n.risk_level, statement=n.risk_statement, confidence=n.confidence, confidence_reason=n.confidence_reason),
            blocks=BUILDERS[s](an)))

    overview = [OverviewRow(number=sec.number, title=sec.title, takeaway=an.narratives.sections[s].takeaway or sec.summary.headline,
                            mix=sec.summary.mix, risk=sec.risk.level) for s, sec in zip(SECTION_ORDER, sections)]

    unique = an.unique_changes()
    mix = Mix()
    for f in unique:
        mix.add(f.impact)
    crit_findings = {f.id: f for f in an.critical_candidates()}
    items = []
    for i, fid in enumerate(an.narratives.critical_order, 1):
        f, n = crit_findings[fid], an.narratives.critical[fid]
        items.append(CriticalChange(rank=i, title=n.title, theme=n.theme, description=n.description,
                                    expiring=chip(f.exp, f.exp_ref), renewal=chip(_crit_renewal(f), f.ren_ref if f.ren not in ("—", "Not on policy") else None),
                                    badge=badge(f), next_step=n.next_step))
    theme_counts = [ThemeCount(theme=t, count=sum(c.theme == t for c in items)) for t in THEME_ORDER]
    improvements = [Improvement(badge=badge(f), text=f"**{_short_label(f.label)}** {_compact(f)} " + (f"[[{f.ren_ref}]]" if f.ren_ref else ""))
                    for f in sorted([f for f in unique if f.impact == "improved"], key=lambda f: SEVERITY_RANK[f.severity])]
    n_items = len(items)
    intro = (f"The {_count_word(n_items)} change{'s' if n_items != 1 else ''} that most affect{'s' if n_items == 1 else ''} this client, ranked by severity and grouped by theme. "
             "Each has a next step to complete before the expiring policy ends.") if items else \
            "No change reduces cover at Critical or High severity. Items to confirm are listed in the sections below."

    prem = an.premium()
    if prem:
        a, b = prem
        delta = b - a
        prem_kpi = Kpi(label="PREMIUM", value=("$0" if not delta else ("−" if delta < 0 else "+") + money(abs(delta))),
                       sub=(f"{pct(delta, a)} · " if delta and a else "unchanged · ") + f"{money(a)} → {money(b)}")
    else:
        prem_kpi = Kpi(label="PREMIUM", value="—", sub="not stated on both policies")
    forms_added = sum(f.kind == "form_added" for f in an.findings["forms"])
    forms_removed = sum(f.kind == "form_removed" for f in an.findings["forms"])
    forms_replaced = sum(f.kind == "form_replaced" for f in an.findings["forms"])
    ex = an.narratives.executive
    verdict = f"**{ex.lead}** {ex.body}"
    if n_items:
        verdict += f" **{plural(n_items, 'critical change')}** need{'s' if n_items == 1 else ''} action before the {an.deadline} expiry."
    executive = ExecutiveSummary(
        risk=ex.risk, confidence=ex.confidence, verdict=verdict, confidence_note=ex.confidence_note,
        kpis=[prem_kpi,
              Kpi(label="CRITICAL CHANGES", value=str(n_items), sub=f"act before {an.deadline}", accent=True),
              Kpi(label="FORMS", value=f"+{forms_added} / −{forms_removed}", sub="added / removed" + (f" · {forms_replaced} replaced" if forms_replaced else "")),
              Kpi(label="TO CONFIRM", value=str(len(an.findings["pending"])), sub="items with the carrier")],
        changes_total=len(unique), mix=mix, actions=_actions(an))

    parts = [p.strip() for p in (next((f.ren for f in an.findings["policy"] if f.kind == "coverage_parts"), "") or "").split(",") if p.strip() and p.strip() != "—"]
    trigger = "Claims-Made" if any(f.label.startswith("Claims-made") and "Claims-Made" in f.exp + f.ren for f in an.findings["checklist"]) else None
    subtitle = " / ".join(parts[:3]) + (f" · {trigger}" if trigger else "") if parts else (R.model.policy.policy_type or "").title()
    today = prepared_on or date.today()
    cover = Cover(agency=brand.get("agency", ""), insured=_title_insured(insured), subtitle=subtitle or "Policy comparison",
                  expiring=PartyCard(carrier=_title_party(E.carrier), policy_number=E.model.policy.policy_number or "—",
                                     period=f"{mdy(E.effective)} – {mdy(E.expiration)}"),
                  renewal=PartyCard(carrier=_title_party(R.carrier), policy_number=R.model.policy.policy_number or "—",
                                    period=f"{mdy(R.effective)} – {mdy(R.expiration)}"),
                  prepared_line=f"Prepared {long_date(today)} by {brand.get('prepared_by', '')}")

    sources = [SourceDoc(ref=s.ref, document=s.file_name, side="Expiring" if s.role == "expiring" else "Renewal policy", type="Policy",
                         pages=s.page_count, how_read=_how_read(s, ti)) for s, ti in ((E, an.ti_e), (R, an.ti_r))]
    meta = Meta(title=f"{_title_insured(insured)} — Policy Renewal Comparison", brand=brand.get("brand", "Fideon OS"),
                agency=brand.get("agency", ""), header_right=f"{_title_insured(insured)} · Renewal Comparison",
                footer_left=brand.get("footer_left", Meta.model_fields["footer_left"].default),
                disclaimer=brand.get("disclaimer", Meta.model_fields["disclaimer"].default))
    return Report(meta=meta, cover=cover, executive=executive,
                  critical=CriticalSection(intro=intro, deadline=an.deadline, themes=theme_counts if items else [], items=items,
                                           improvements=improvements, empty_note=None if items else
                                           "No critical changes found — nothing on the renewal reduces cover at Critical or High severity."),
                  overview=overview, sections=sections, sources=sources, drafts=an.drafts, audit=an.audit)


def _crit_renewal(f: Finding) -> str:
    return "Not found" if f.ren in ("—", "Not on policy", "Not found") else f.ren


def _short_label(label: str) -> str:
    return re.sub(r"\s+(Limit|—.*)$", "", label)


def _compact(f: Finding) -> str:
    a, b = f.context.get("expiring_amount"), f.context.get("renewal_amount")
    if a is not None and b is not None:
        return f"{money_compact(a)} → {money_compact(b)}"
    return f"{f.exp} → {f.ren}"


def _count_word(n: int) -> str:
    words = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
    return words[n] if n < len(words) else str(n)


def _how_read(side, ti) -> str:
    if side.ocr_pages:
        return f"Digital + {plural(len(side.ocr_pages), 'scanned page')} read by the vision model"
    scanned = len(ti.scanned_pages())
    base = "Digital text"
    return f"{base} + {plural(scanned, 'scanned page')}" if scanned else base


def _title_party(name: str) -> str:
    return name.title() if name.isupper() else name


def _title_insured(name: str) -> str:
    if not name.isupper():
        return name
    t = name.title()
    return re.sub(r"\b(Inc|Llc|Co|Corp|Ltd|Lp|Llp)\b", lambda m: {"Llc": "LLC", "Lp": "LP", "Llp": "LLP"}.get(m.group(1), m.group(1)), t)
