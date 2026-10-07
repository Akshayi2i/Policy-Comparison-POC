"""Build tests/fixtures/summit_ridge.report.json: a verbatim transcription of the reference PDF
(Comparison_Document_Colour.pdf) into the Report model. Used to prove template fidelity independently
of the engine and the model.

usage: python -m tests.fixtures.make_summit_ridge
"""
from pathlib import Path

from policy_compare.schema.report import (
    Badge, Cover, CriticalChange, CriticalSection, ExecutiveSummary, FocusBlock, FocusCard, Improvement, Kpi,
    Meta, Mix, OverviewRow, PartyCard, QuoteLine, Report, RiskAnalysis, Row, Section, SectionSummary, SourceDoc,
    TableBlock, ThemeCount,
)

RC, RH = Badge(impact="reduced", severity="critical"), Badge(impact="reduced", severity="high")
CH, CM, CL = (Badge(impact="confirm", severity=s) for s in ("high", "medium", "low"))
NL = Badge(impact="no_impact", severity="low")
IM, IL = Badge(impact="improved", severity="medium"), Badge(impact="improved", severity="low")

CMP = ["Element", "Expiring", "Renewal", "Change / Assessment"]
FORMS = ["Form", "Expiring", "Renewal", "Effect"]
NOT_FOUND = "{x} was found in the prior documents ({v}) but not in the renewal. Either it was removed or the renewal states it differently - check the renewal."
CONTRACT_WHY = "Contracts that require it would be breached, and certificates issued on expiring terms would overstate coverage."


def mix(r=0, c=0, n=0, i=0) -> Mix:
    return Mix(reduced=r, confirm=c, no_impact=n, improved=i)


def form_row(label, exp, ren, change, badge, explain):
    return Row(label=label, expiring=exp, renewal=ren, change=change, badge=badge, explain_label="What it does", explain=explain)


def build() -> Report:
    sections = [
        Section(
            number="3", title="Policy Information", page_break_before=True,
            summary=SectionSummary(
                headline="New carrier, new market status and a dropped coverage part - this is a re-marketed placement, not a like-for-like renewal.",
                bullets=["11 h 59 min gap between expiry and inception.", "Commercial Property is not part of the renewal.", "Renewal is non-admitted (surplus lines)."],
                mix=mix(3, 1, 3), items_compared=7),
            risk=RiskAnalysis(level="high", statement="Three structural changes alter who insures the client, what is insured and when cover starts.",
                              confidence="high", confidence_reason="All values read from digital declarations and schedules (E1 p.1–2, R1 p.1–2)."),
            blocks=[TableBlock(columns=CMP, rows=[
                Row(label="Named Insured", expiring="Summit Ridge Builders, Inc. [[E1 p.1]]", renewal="Summit Ridge Builders Inc [[R1 p.1]]", change="No change"),
                Row(label="Policy period continuity", expiring="Expires 03/01/2026 [[E1 p.1]]", renewal="Incepts 03/01/2026 [[R1 p.1]]", change="Gap of 11 h 59 min", badge=RC,
                    explain="The renewal starts 11 h 59 min after the expiring policy ends, so there is no coverage in between."),
                Row(label="Coverage parts (lines of business)", expiring="Commercial Property, General Liability [[E1 p.2]]", renewal="General Liability [[R1 p.2]]",
                    change="Dropped: Commercial Property", badge=RC,
                    explain="Commercial Property appears on the expiring policy but not in the renewal documents. Either the coverage part is not being renewed (the client needs replacement cover or written notice) or it is quoted separately - confirm before the expiration date."),
                Row(label="Admitted / surplus lines status", expiring="No surplus lines wording found", renewal="Non-admitted (surplus lines) [[R1 p.1]]", change="Now non-admitted", badge=RH,
                    explain="A non-admitted (surplus lines) policy is not backed by the state guaranty fund, carries surplus lines tax and filing duties, and may need diligent-effort documentation."),
                Row(label="Insurer", expiring="Keystone Casualty Company [[E1 p.1]]", renewal="Granite Specialty Insurance Company [[R1 p.1]]", change="Keystone Casualty → Granite Specialty", badge=CM,
                    explain="Insurer changed. Confirm AM Best rating, reissue certificates of insurance and compare full policy forms - carrier-specific forms often differ from ISO wording."),
                Row(label="Policy Number", expiring="KCC-GL-25-77310 [[E1 p.1]]", renewal="GSI-CGL-2611-0042 [[R1 p.1]]", change="New policy number", badge=NL),
                Row(label="Effective Date", expiring="03/01/2025 [[E1 p.1]]", renewal="03/01/2026 [[R1 p.1]]", change="03/01/2025 → 03/01/2026", badge=NL),
                Row(label="Expiration Date", expiring="03/01/2026 [[E1 p.1]]", renewal="03/01/2027 [[R1 p.1]]", change="03/01/2026 → 03/01/2027", badge=NL),
            ])],
        ),
        Section(
            number="4", title="Premium and Financial Terms",
            summary=SectionSummary(
                headline="Premium falls 11% ($3,500), but a new $5,000 liability SIR shifts part of that saving back to the client.",
                bullets=["Expiring was +$2,150 higher after mid-term changes.", "Property deductible not restated on the renewal.", "Wind / Hail deductible not shown on either declarations page."],
                mix=mix(1, 1, 1), items_compared=3),
            risk=RiskAnalysis(level="medium", statement="The saving is real but partly offset by retained risk and reduced cover elsewhere in the report.",
                              confidence="medium", confidence_reason="Premium is clearly stated; deductibles may sit in forms rather than declarations, so absences are unconfirmed."),
            blocks=[
                TableBlock(heading="Premium", columns=CMP, rows=[
                    Row(label="Total Premium", expiring="$31,400 [[E1 p.1]]", renewal="$27,900 [[R1 p.1]]", change="Decreased $3,500 (−11.1%)", badge=NL,
                        explain="Informational. Mid-term adjustments on the expiring policy added +$2,150 (Endorsement No. 2, eff. 09/15/2025, E1 p.6); the expiring figure is the annual declarations premium before those adjustments."),
                ]),
                TableBlock(heading="Deductibles and retentions", columns=CMP, rows=[
                    Row(label="Liability Deductible / SIR", expiring="None [[E1 p.1]]", renewal="$5,000 [[R1 p.1]]", change="None → $5,000", badge=RH,
                        explain="The insured now retains the first $5,000 of every liability loss. Part of the premium saving is transferred back to the client as retained risk."),
                    Row(label="All Other Perils Deductible", expiring="$2,500 [[E1 p.1]]", renewal="—", change="Not found in renewal", badge=CM,
                        explain=NOT_FOUND.format(x="All Other Perils Deductible", v="$2,500")),
                ], footnote="Not shown on either declarations page (may be set by the policy forms): Wind / Hail Deductible."),
            ],
        ),
        Section(
            number="5", title="Limits and Sub-limits",
            summary=SectionSummary(
                headline="Per-occurrence limit is halved and products-completed operations is excluded; the aggregate increase does not compensate.",
                bullets=["Each Occurrence $2M → $1M (mid-term increase lost).", "Fire legal liability removed.", "Building / BPP limits absent with the property part."],
                mix=mix(3, 2, 0, 2), items_compared=7),
            risk=RiskAnalysis(level="high", statement="Two critical limit reductions hit the contractor's main liability exposure.",
                              confidence="medium", confidence_reason="Liability limits are clear; property limits may be quoted separately. Business Income / Extra Expense not shown on either policy."),
            blocks=[TableBlock(columns=["Limit", "Expiring", "Renewal", "Change / Assessment"], rows=[
                Row(label="Each Occurrence Limit", tag="AMENDED MID-TERM", expiring="$2,000,000 [[E1 p.6]]", renewal="$1,000,000 [[R1 p.1]]", change="Decreased $1,000,000 (−50%)", badge=RC,
                    explain="The limit caps what the insurer pays per claim; halving it leaves materially more uninsured exposure. The expiring $2M was set by a mid-term endorsement that the renewal did not carry forward."),
                Row(label="Products-Completed Operations Aggregate", expiring="Included [[E1 p.1]]", renewal="Excluded [[R1 p.1]]", change="Included → Excluded", badge=RC,
                    explain="For a contractor, completed-operations claims are a core exposure. The coverage is no longer provided - a significant narrowing."),
                Row(label="Damage to Premises Rented / Fire Legal Liability", expiring="$100,000 [[E1 p.1]]", renewal="None [[R1 p.1]]", change="$100,000 → None", badge=RH,
                    explain="Fire damage to rented premises is no longer covered."),
                Row(label="General Aggregate Limit", expiring="$2,000,000 [[E1 p.1]]", renewal="$3,000,000 [[R1 p.1]]", change="Increased $1,000,000 (+50%)", badge=IM,
                    explain="More total capacity across the policy year - partly offset by the lower per-occurrence limit."),
                Row(label="Medical Expense (any one person)", expiring="$5,000 [[E1 p.1]]", renewal="$10,000 [[R1 p.1]]", change="Increased $5,000 (+100%)", badge=IL,
                    explain="Higher no-fault medical payments to injured third parties."),
                Row(label="Personal & Advertising Injury", expiring="$1,000,000 [[E1 p.1]]", renewal="$1,000,000 [[R1 p.1]]", change="No change"),
                Row(label="Building Limit", expiring="$1,800,000 [[E1 p.1]]", renewal="—", change="Not found in renewal", badge=CH,
                    explain=NOT_FOUND.format(x="Building Limit", v="$1,800,000")),
                Row(label="Business Personal Property", expiring="$400,000 [[E1 p.1]]", renewal="—", change="Not found in renewal", badge=CH,
                    explain=NOT_FOUND.format(x="Business Personal Property", v="$400,000")),
            ], footnote="Not shown on either declarations page: Business Income / Extra Expense.")],
        ),
        Section(
            number="6", title="Coverage Terms and Conditions",
            summary=SectionSummary(
                headline="Retro date advanced to inception removes prior-acts cover; terrorism is rejected and notice of cancellation is cut to 10 days.",
                bullets=["Loss of seven years of prior acts.", "TRIA rejection needs a signed client form.", "Valuation, coinsurance and causes of loss not restated."],
                mix=mix(3, 3), items_compared=6),
            risk=RiskAnalysis(level="high", statement="The retro change is one of the most consequential findings for a claims-made contractor policy.",
                              confidence="medium", confidence_reason="R1 includes one scanned page; property terms could not be located and are pending confirmation."),
            blocks=[TableBlock(columns=["Term", "Expiring", "Renewal", "Change / Assessment"], rows=[
                Row(label="Coverage Trigger", expiring="Claims-Made [[E1 p.1]]", renewal="Claims-Made [[R1 p.1]]", change="No change"),
                Row(label="Retroactive Date", expiring="01/01/2019 [[E1 p.1]]", renewal="03/01/2026 [[R1 p.1]]", change="Advanced 01/01/2019 → 03/01/2026", badge=RC,
                    explain="Incidents before 03/01/2026 that have not yet been claimed are no longer covered (loss of prior acts). Request the expiring retro date or price an extended reporting period."),
                Row(label="Terrorism (TRIA) election / premium", expiring="$310 (accepted) [[E1 p.1]]", renewal="Rejected [[R1 p.1]]", change="Accepted → Rejected", badge=RH,
                    explain="Terrorism cover is removed. The election is a documented client decision; a signed rejection must be on file."),
                Row(label="Notice of Cancellation", expiring="30 days [[E1 p.3]]", renewal="10 days [[R1 p.3]]", change="Decreased 30 → 10 days", badge=RH,
                    explain="Less time for the insured, lenders and certificate holders to arrange replacement cover - check contract and lender requirements."),
                Row(label="Valuation", expiring="Replacement Cost [[E1 p.1]]", renewal="—", change="Not found in renewal", badge=CM,
                    explain=NOT_FOUND.format(x="Valuation", v="Replacement Cost")),
                Row(label="Coinsurance", expiring="80% [[E1 p.1]]", renewal="—", change="Not found in renewal", badge=CM,
                    explain=NOT_FOUND.format(x="Coinsurance", v="80%")),
                Row(label="Causes of Loss Form", expiring="Special [[E1 p.1]]", renewal="—", change="Not found in renewal", badge=CM,
                    explain=NOT_FOUND.format(x="Causes of Loss Form", v="Special")),
                Row(label="Premium Audit", expiring="Annual, payroll basis [[E1 p.3]]", renewal="Annual, payroll basis [[R1 p.3]]", change="No change"),
            ])],
        ),
        Section(
            number="7", title="Endorsements and Forms",
            summary=SectionSummary(
                headline="Five forms removed and one restrictive exclusion added; three removals breach typical construction contract requirements.",
                bullets=["1 added · 5 removed · 1 edition change · 2 wording changes.", "CG 20 01, CG 20 37 and CG 25 03 are gone.", "Silica exclusion wording appears broader."],
                mix=mix(4, 5), items_compared=9),
            risk=RiskAnalysis(level="high", statement="Lost contract-required forms create both coverage gaps and certificate-of-insurance exposure.",
                              confidence="medium", confidence_reason="Form numbers matched on schedules with high certainty; the silica and CG 20 10 wording effects need a full-form read."),
            blocks=[
                TableBlock(variant="forms", heading="Forms removed", heading_note="on expiring, not on renewal", columns=FORMS, rows=[
                    form_row("CG 20 01 — Primary and Noncontributory - Other Insurance Condition", "CG 20 01 04/13 [[E1 p.2]]", "Not on policy", "Removed at renewal", RC,
                             "Makes coverage for qualifying additional insureds primary and noncontributory, often required by contracts."),
                    form_row("CG 20 37 — Additional Insured - Owners, Lessees or Contractors - Completed Operations", "CG 20 37 04/13 [[E1 p.2]]", "Not on policy", "Removed at renewal", RC,
                             "Extends additional insured status to completed operations; frequently required by construction contracts."),
                    form_row("CG 25 03 — Designated Construction Project(s) General Aggregate Limit", "CG 25 03 05/09 [[E1 p.2]]", "Not on policy", "Removed at renewal", RC,
                             "Applies a separate general aggregate limit to each designated construction project."),
                    form_row("CP 00 10 — Building and Personal Property Coverage Form", "CP 00 10 10/12 [[E1 p.2]]", "Not on policy", "Removed at renewal", CH,
                             "Core commercial property coverage form for buildings and business personal property."),
                    form_row("CP 10 30 — Causes of Loss - Special Form", "CP 10 30 10/12 [[E1 p.2]]", "Not on policy", "Removed at renewal", CH,
                             "Open-peril ('all risk') property coverage subject to listed exclusions; the broadest standard form."),
                ]),
                TableBlock(variant="forms", heading="Forms added", heading_note="new on renewal", columns=FORMS, rows=[
                    form_row("CG 21 47 — Employment-Related Practices Exclusion", "Not on policy", "CG 21 47 12/07 [[R1 p.2]]", "Added on renewal", RH,
                             "Excludes injury arising from employment practices (wrongful termination, harassment, discrimination)."),
                ]),
                TableBlock(variant="forms", heading="Edition changes", columns=FORMS, rows=[
                    form_row("CG 20 10 — Additional Insured - Owners, Lessees or Contractors - Scheduled Person or Organization", "CG 20 10 04/13 [[E1 p.2]]", "CG 20 10 12/19 [[R1 p.2]]",
                             "Edition 04/13 → 12/19", CM,
                             "Adds scheduled parties as additional insureds for ongoing operations. Newer editions can narrow AI status to “required by written contract” and to the limits the contract requires."),
                ]),
                TableBlock(variant="forms", heading="Wording changes", heading_note="same form, different text", columns=FORMS, rows=[
                    Row(label="Endorsement No. 3 - Silica and Respirable Dust Exclusion", expiring="Present [[E1 p.5]]", renewal="Present [[R1 p.5]]",
                        change="“except where” → “whether or not”", badge=CH, explain_label="Wording",
                        explain="expiring “except where” [[E1 p.5]] → renewal “whether or not” [[R1 p.5]]. The change in conjunction likely removes a carve-back, broadening the exclusion - a client focus area."),
                    Row(label="IL 00 17 — Common Policy Conditions", expiring="IL 00 17 11/98 [[E1 p.4]]", renewal="IL 00 17 11/98 [[R1 p.4]]",
                        change="“30” → “10”", badge=CM, explain_label="Wording",
                        explain="expiring “30” [[E1 p.4]] → renewal “10” [[R1 p.4]] - the notice-of-cancellation reduction in Section 6."),
                ], footnote="**Forms unchanged:** CG 00 02 04/13 [[R1 p.2]], CG 24 04 05/09 [[R1 p.2]]."),
            ],
        ),
        Section(
            number="8", title="Mid-term Changes on the Expiring Policy",
            summary=SectionSummary(
                headline="The September 2025 limit increase was applied to the expiring policy but not carried into the renewal.",
                bullets=["Expiring column reflects the policy as amended.", "1 change applied; none superseded."],
                mix=mix(1, 1), items_compared=2),
            risk=RiskAnalysis(level="high", statement="Clients often assume mid-term increases persist; this one silently lapses at renewal.",
                              confidence="medium", confidence_reason="Endorsement No. 2 references a form (CG 21 53) that is not on the policy."),
            blocks=[TableBlock(intro="The expiring column in this report is the policy as amended by these changes, applied in effective-date order.",
                               columns=["Step", "Expiring value", "Renewal", "Result"], rows=[
                Row(label="Each Occurrence Limit — Declarations", expiring="$1,000,000 [[E1 p.1]]", renewal="", change="Stated on Declarations (page 1)"),
                Row(label="Policy change · Endorsement No. 2 · eff. 09/15/2025", expiring="$2,000,000 [[E1 p.6]]", renewal="$1,000,000 [[R1 p.1]]",
                    change="Mid-term change not carried into renewal", badge=RC,
                    explain="The renewal reverts to the original declarations. Carriers often build renewals from the original policy rather than the endorsed policy - ask the underwriter to carry the change forward."),
                Row(label="Policy change · Endorsement No. 2 · eff. 09/15/2025", expiring="Deleted CG 21 53 [[E1 p.6]]", renewal="",
                    change="Applied - but CG 21 53 01/96 was not found on the policy", badge=CL,
                    explain="Deletion references a form that was never scheduled; confirm with the expiring carrier that no other form was intended."),
            ])],
        ),
        Section(
            number="9", title="Contract Requirements and Broker Checklist",
            summary=SectionSummary(
                headline="Three contract requirements met on the expiring policy are not met on the renewal; ongoing-ops AI and waiver of subrogation carry over.",
                bullets=["Hold certificate reissue until forms are confirmed.", "Renewal Endorsement No. 1 has no effective date."],
                mix=mix(5, 2), items_compared=7),
            risk=RiskAnalysis(level="high", statement="Breach of client contracts is likely if the renewal binds as quoted.",
                              confidence="high", confidence_reason="Requirements matched on form numbers listed on both forms schedules."),
            blocks=[TableBlock(columns=["Check", "Expiring", "Renewal", "Change / Assessment"], rows=[
                Row(label="Contract requirement: Additional insured - completed operations", expiring="Yes - CG 20 37 [[E1 p.2]]", renewal="Not found",
                    change="Lost on renewal", badge=RC, explain=CONTRACT_WHY),
                Row(label="Contract requirement: Primary & noncontributory", expiring="Yes - CG 20 01 [[E1 p.2]]", renewal="Not found",
                    change="Lost on renewal", badge=RC, explain=CONTRACT_WHY),
                Row(label="Contract requirement: Per project / per location aggregate", expiring="Yes - CG 25 03 [[E1 p.2]]", renewal="Not found",
                    change="Lost on renewal", badge=RC, explain=CONTRACT_WHY),
                Row(label="Claims-made continuity (retroactive date)", expiring="Claims-Made; retro 01/01/2019 [[E1 p.1]]", renewal="Claims-Made; retro 03/01/2026 [[R1 p.1]]",
                    change="Retro advanced", badge=RC, explain="Loss of prior acts - see Section 6."),
                Row(label="Mid-term changes carried into renewal", expiring="1 value changed mid-term [[E1 p.6]]", renewal="1 not carried forward [[R1 p.1]]",
                    change="Each Occurrence Limit", badge=RC, explain="Expiring as endorsed $2,000,000; renewal $1,000,000 - see Section 8."),
                Row(label="Carrier change", expiring="Keystone Casualty Company [[E1 p.1]]", renewal="Granite Specialty Insurance Company [[R1 p.1]]",
                    change="New carrier", badge=CM, explain="Confirm AM Best rating and admitted status, reissue certificates, check lender/landlord notice requirements."),
                Row(label="Endorsement wording that needs a read", expiring="1 note [[E1 p.6]]", renewal="1 note [[R1 p.6]]", change="2 notes", badge=CL,
                    explain="[expiring] Endorsement No. 2 deletes CG 21 53 01/96, not found on the policy • [renewal] Endorsement No. 1 has no effective date - applied after dated changes; review."),
                Row(label="Contract requirement: Additional insured - ongoing operations", expiring="Yes - CG 20 10 [[E1 p.2]]", renewal="Yes - CG 20 10 [[R1 p.2]]", change="No change"),
                Row(label="Contract requirement: Waiver of subrogation", expiring="Yes - CG 24 04 [[E1 p.2]]", renewal="Yes - CG 24 04 [[R1 p.2]]", change="No change"),
                Row(label="Excess and umbrella tower", expiring="—", renewal="—", change="Out of scope in comparison mode"),
            ])],
        ),
        Section(
            number="10", title="Client Focus Areas",
            summary=SectionSummary(
                headline="All three topics the client asked about are affected by the renewal; silica is the one most likely to surprise them.",
                bullets=["Silica exclusion wording broadened.", "4 additional-insured changes, 3 of them narrower.", "Terrorism rejected."],
                mix=mix(2, 1), items_compared=3),
            risk=RiskAnalysis(level="medium", statement="Each focus area has at least one less favourable change the client should hear about directly.",
                              confidence="high", confidence_reason="Every finding is backed by a verbatim quote with a page reference."),
            blocks=[FocusBlock(cards=[
                FocusCard(topic="silica", badge=CH, mentions="3 mentions in prior · 3 in renewal",
                          related="Related change: Endorsement No. 3 - Silica and Respirable Dust Exclusion (wording changed, review).",
                          quotes=[QuoteLine(side="expiring", text="This insurance does not apply to bodily injury arising out of the inhalation of silica dust,", ref="E1 p.5"),
                                  QuoteLine(side="renewal", text="This insurance does not apply to bodily injury arising out of the inhalation of silica dust,", ref="R1 p.5")]),
                FocusCard(topic="additional insured", badge=RC, mentions="2 mentions in prior · 2 in renewal",
                          related="Related changes: AI - completed operations (narrower); CG 20 01 (removed, narrower); CG 20 37 (removed, narrower); CG 20 10 (edition changed, review).",
                          quotes=[QuoteLine(side="expiring", text="CG 20 37 04 13 Additional Insured - Completed Operations", ref="E1 p.2"),
                                  QuoteLine(side="renewal", text="CG 20 10 12 19 Additional Insured - Owners, Lessees or Contractors", ref="R1 p.2"),
                                  QuoteLine(side="renewal", text="The schedule of the Additional Insured endorsement CG 20 10 is amended to add Front Range Water", ref="R1 p.6")]),
                FocusCard(topic="terrorism", badge=RH, mentions="1 mention in prior · 1 in renewal",
                          quotes=[QuoteLine(side="expiring", text="Terrorism (TRIA) Premium: $310", ref="E1 p.1"),
                                  QuoteLine(side="renewal", text="Terrorism (TRIA) Premium: Rejected", ref="R1 p.1")]),
            ])],
        ),
        Section(
            number="11", title="Items Pending Confirmation with the Carrier",
            summary=SectionSummary(
                headline="Both open items concern the missing Commercial Property part - one carrier answer resolves them.",
                bullets=["Confirm whether property is quoted separately.", "If not, market property cover before 03/01/2026."],
                mix=mix(0, 2), items_compared=2),
            risk=RiskAnalysis(level="medium", statement="Risk becomes Critical if property is not being renewed anywhere.",
                              confidence="low", confidence_reason="Absence of forms is clear; the reason for the absence is unknown."),
            blocks=[TableBlock(variant="pending", columns=["Item", "Expiring", "Renewal", "Next step"], rows=[
                Row(label="CP 00 10 — Building and Personal Property Coverage Form",
                    sublabel="Core commercial property coverage form for buildings and business personal property.",
                    expiring="CP 00 10 10/12 [[E1 p.2]]", renewal="—", change="Pending confirmation with the carrier", badge=CH),
                Row(label="CP 10 30 — Causes of Loss - Special Form",
                    sublabel="Open-peril ('all risk') property coverage subject to listed exclusions.",
                    expiring="CP 10 30 10/12 [[E1 p.2]]", renewal="—", change="Pending confirmation with the carrier", badge=CH),
            ])],
        ),
    ]

    overview = [OverviewRow(number=s.number, title=s.title, takeaway=t, mix=s.summary.mix, risk=s.risk.level) for s, t in zip(sections, [
        sections[0].summary.headline, sections[1].summary.headline, sections[2].summary.headline, sections[3].summary.headline,
        sections[4].summary.headline, sections[5].summary.headline, sections[6].summary.headline,
        "Silica exclusion broadened; additional insured changes; terrorism rejected.",
        "Two property forms removed without explanation.",
    ])]

    def cc(rank, title, theme, desc, exp, ren, badge, step):
        return CriticalChange(rank=rank, title=title, theme=theme, description=desc, expiring=exp, renewal=ren, badge=badge, next_step=step)

    contract_desc = "Contract breach risk; existing COIs overstate coverage."
    return Report(
        meta=Meta(title="Summit Ridge Builders Inc — Policy Renewal Comparison", agency="Fideon Demo Agency",
                  header_right="Summit Ridge Builders Inc · Renewal Comparison"),
        cover=Cover(agency="Fideon Demo Agency", insured="Summit Ridge Builders Inc", subtitle="Commercial Property / General Liability · Claims-Made",
                    expiring=PartyCard(carrier="Keystone Casualty Company", policy_number="KCC-GL-25-77310", period="03/01/2025 – 03/01/2026"),
                    renewal=PartyCard(carrier="Granite Specialty Insurance Company", policy_number="GSI-CGL-2611-0042", period="03/01/2026 – 03/01/2027"),
                    prepared_line="Prepared October 06, 2026 by Account Manager (demo)"),
        executive=ExecutiveSummary(
            risk="high", confidence="medium",
            verdict="**Do not present this renewal as like-for-like.** Premium is $3,500 lower, but the renewal moves to a surplus lines carrier, drops Commercial Property, advances the retro date to inception and removes three contract-required endorsements. **10 critical changes** need action before the 03/01/2026 expiry.",
            confidence_note="Confidence is Medium because Commercial Property may be quoted separately and one renewal page was scanned.",
            kpis=[Kpi(label="PREMIUM", value="−$3,500", sub="−11.1% · $31,400 → $27,900"),
                  Kpi(label="CRITICAL CHANGES", value="10", sub="act before 03/01/2026", accent=True),
                  Kpi(label="FORMS", value="+1 / −5", sub="added / removed"),
                  Kpi(label="TO CONFIRM", value="2", sub="items with the carrier")],
            changes_total=41, mix=mix(18, 17, 4, 2)),
        critical=CriticalSection(
            intro="The ten changes that most affect this client, ranked by severity and grouped by theme. Each has a next step to complete before the expiring policy ends.",
            deadline="03/01/2026",
            themes=[ThemeCount(theme=t, count=n) for t, n in [("continuity", 2), ("scope", 2), ("limits", 2), ("contracts", 3), ("market", 1)]],
            items=[
                cc(1, "Policy period gap", "continuity", "No coverage between the expiring and renewal policy.", "Expires 03/01/2026 [[E1 p.1]]",
                   "Incepts 03/01/2026 (+11 h 59 min) [[R1 p.1]]", RC, "Ask carrier to align inception time to 12:01 AM."),
                cc(2, "Retroactive date advanced", "continuity", "Seven years of prior acts lose coverage on a claims-made form.", "Retro 01/01/2019 [[E1 p.1]]",
                   "Retro 03/01/2026 [[R1 p.1]]", RC, "Negotiate 01/01/2019 retro or quote an ERP tail."),
                cc(3, "Commercial Property dropped", "scope", "Building ($1.8M) and BPP ($400K) have no cover on the renewal.", "Property + GL [[E1 p.2]]",
                   "GL only [[R1 p.2]]", RC, "Confirm separate property quote before 03/01/2026."),
                cc(4, "Each Occurrence limit halved", "limits", "Mid-term increase was not carried forward.", "$2,000,000 [[E1 p.6]]",
                   "$1,000,000 [[R1 p.1]]", RC, "Ask underwriter to reinstate $2M endorsement."),
                cc(5, "Products-completed ops excluded", "limits", "Core contractor exposure is no longer insured.", "Included [[E1 p.1]]",
                   "Excluded [[R1 p.1]]", RC, "Request reinstatement; flag to client in writing."),
                cc(6, "AI – completed operations lost", "contracts", contract_desc, "CG 20 37 [[E1 p.2]]", "Not found", RC, "Request CG 20 37; hold COI reissue."),
                cc(7, "Primary & noncontributory lost", "contracts", contract_desc, "CG 20 01 [[E1 p.2]]", "Not found", RC, "Request CG 20 01 on renewal."),
                cc(8, "Per-project aggregate lost", "contracts", "One aggregate now shared across all job sites.", "CG 25 03 [[E1 p.2]]", "Not found", RC,
                   "Request CG 25 03 on renewal."),
                cc(9, "Now non-admitted (surplus lines)", "market", "No state guaranty fund; SL tax and filing duties apply.", "Admitted",
                   "Surplus lines [[R1 p.1]]", RH, "Disclose to client; complete diligent-effort file."),
                cc(10, "Terrorism (TRIA) rejected", "scope", "Terrorism losses are uninsured.", "$310 accepted [[E1 p.1]]", "Rejected [[R1 p.1]]", RH,
                   "Obtain signed rejection or re-elect cover."),
            ],
            improvements=[Improvement(badge=IM, text="**General Aggregate** $2M → $3M [[R1 p.1]]"),
                          Improvement(badge=IL, text="**Medical Expense** $5,000 → $10,000 [[R1 p.1]]")]),
        overview=overview,
        sections=sections,
        sources=[SourceDoc(ref="E1", document="summit_expiring_2025.pdf", side="Expiring", type="Policy", pages=7, how_read="Digital text"),
                 SourceDoc(ref="R1", document="summit_renewal_2026.pdf", side="Renewal policy", type="Policy", pages=7, how_read="Digital + 1 scanned page")],
    )


if __name__ == "__main__":
    out = Path(__file__).with_name("summit_ridge.report.json")
    out.write_text(build().model_dump_json(indent=1), encoding="utf-8")
    print(out)
