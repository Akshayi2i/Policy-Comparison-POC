"""D: ready-to-send drafts — questions for the carrier and a plain-English renewal letter for the client.

Rule-based drafts are always produced from the findings; the model rewrites them when available, through the same
gate as all other model text (no invented numbers, no claims that contradict the forms schedule).
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Optional

from policy_compare.analysis.guards import TextGate
from policy_compare.fmt import mdy, money
from policy_compare.llm.client import LLMClient, LLMError, prompt
from policy_compare.schema.llm_io import Drafts
from policy_compare.schema.report import DraftText, ReportDrafts
from policy_compare.settings import config

if TYPE_CHECKING:
    from policy_compare.engine import Analysis

MAX_ITEMS = 8


def _carrier_items(an: "Analysis") -> list[str]:
    items: list[str] = []
    crit = {f.id: f for f in an.critical_candidates()}
    for fid in an.narratives.critical_order:
        f, n = crit.get(fid), an.narratives.critical.get(fid)
        if f and n:
            items.append(f"{f.label} ({f.change}): {n.next_step}")
    for f in an.findings.get("pending", []):
        items.append(f"{f.label}: this form is on the expiring policy but not on the renewal. "
                     "Please confirm whether it was removed intentionally and, if so, why.")
    seen = {i.split(":")[0] for i in items}
    for f in an.unique_changes():
        if f.impact == "confirm" and f.label not in seen and f.section not in ("focus", "checklist"):
            items.append(f"{f.label}: {f.change}. Please confirm the effect on cover.")
    return items[:MAX_ITEMS]


def _client_points(an: "Analysis") -> tuple[list[str], list[str]]:
    changes = [f"{f.label}: {f.change[:1].lower() + f.change[1:]}." for f in an.unique_changes()
               if f.impact in ("reduced", "improved", "confirm") and f.section not in ("checklist", "focus", "pending")][:6]
    discuss = [f"{f.label}." for f in an.findings.get("observations", [])][:3]
    return changes, discuss


def default_drafts(an: "Analysis") -> ReportDrafts:
    b = config("branding")
    E, R = an.E, an.R
    insured = R.insured or E.insured
    num = R.model.policy.policy_number or E.model.policy.policy_number or ""
    sign = f"{b.get('prepared_by', '')}\n{b.get('agency', '')}".strip()
    items = _carrier_items(an)
    lines = "\n".join(f"{i}. {t}" for i, t in enumerate(items, 1)) or \
        "No open questions were identified. Please confirm the renewal terms are as quoted."
    carrier = DraftText(
        subject=f"Renewal of policy {num} for {insured}: questions before {an.deadline}",
        body=(f"Hello,\n\nWe are reviewing the renewal of policy {num} for {insured} "
              f"(renewal term {mdy(R.effective)} to {mdy(R.expiration)}). Before the current policy expires on "
              f"{an.deadline}, please confirm the following:\n\n{lines}\n\nThank you,\n{sign}"))
    prem = an.premium()
    if prem:
        a, c = prem
        prem_line = "Your premium is unchanged." if a == c else \
            f"Your premium changes from {money(a)} to {money(c)}."
    else:
        prem_line = ""
    changes, discuss = _client_points(an)
    body = [f"Dear {insured},", "",
            f"Your policy {num} with {R.carrier or 'your insurer'} renews on {mdy(R.effective)}. {prem_line}".strip(), ""]
    if changes:
        body += ["What changes at renewal:"] + [f"- {c}" for c in changes] + [""]
    else:
        body += ["We found no changes to your cover at this renewal.", ""]
    if an.findings.get("pending"):
        body += ["We are confirming some of these points with the insurer and will let you know the outcome.", ""]
    if discuss:
        body += ["Points we would like to discuss with you (these did not change, but matter for your work):"] + \
                [f"- {d}" for d in discuss] + [""]
    body += ["Please contact us with any questions.", "", "Kind regards,", sign]
    client = DraftText(subject=f"Your policy renewal {mdy(R.effective)}: summary of changes", body="\n".join(body))
    return ReportDrafts(carrier_email=carrier, client_letter=client, source="default")


def build_drafts(an: "Analysis", llm: Optional[LLMClient]) -> ReportDrafts:
    d = default_drafts(an)
    if not llm:
        return d
    from policy_compare.analysis.assess import policy_context
    b = config("branding")
    facts = {
        "policy": json.loads(policy_context(an)),
        "premium": [money(x) for x in an.premium()] if an.premium() else None,
        "expiry_deadline": an.deadline,
        "carrier_questions": _carrier_items(an),
        "changes": [{"label": f.label, "change": f.change, "impact": f.impact} for f in an.unique_changes()][:30],
        "observations": [{"concern": f.label, "recommendation": f.change} for f in an.findings.get("observations", [])],
        "sender": {"name": b.get("prepared_by"), "agency": b.get("agency")},
    }
    try:
        res = llm.structured("drafts", prompt("drafts").format(facts=json.dumps(facts, ensure_ascii=False, indent=1)), Drafts)
    except LLMError as e:
        an.warnings.append(f"draft messages failed; rule-based drafts used ({e})")
        return d
    gate = TextGate(an)

    def pick(field: str, text: str, limit: int, default: str, kind: str = "text") -> str:
        return gate.take(field, text, limit, kind=kind) or default

    return ReportDrafts(
        carrier_email=DraftText(subject=pick("carrier email subject", res.carrier_email.subject, 18, d.carrier_email.subject),
                                body=pick("carrier email body", res.carrier_email.body, 320, d.carrier_email.body, "letter")),
        client_letter=DraftText(subject=pick("client letter subject", res.client_letter.subject, 18, d.client_letter.subject),
                                body=pick("client letter body", res.client_letter.body, 320, d.client_letter.body, "letter")),
        source="model")


def write_drafts(drafts: ReportDrafts, stem_path) -> list:
    """Save the drafts as plain-text files next to the PDF: <stem>.carrier_email.txt and <stem>.client_letter.txt."""
    out = []
    for name, msg in (("carrier_email", drafts.carrier_email), ("client_letter", drafts.client_letter)):
        p = stem_path.with_name(f"{stem_path.name}.{name}.txt")
        p.write_text(f"Subject: {msg.subject}\n\n{msg.body}\n", encoding="utf-8")
        out.append(p)
    return out
