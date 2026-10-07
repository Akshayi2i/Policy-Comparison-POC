"""ReportModel: everything the PDF template renders.

The engine fills this model; the renderer only reads it. Text fields marked "rich" accept a
tiny markup understood by the template filter `rich`:
    **bold**        -> <strong>
    *italic*        -> <em>
    [[E1 p.6]]      -> page-reference chip
All numbers and values in the model are produced by code, never by the language model.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field

Impact = Literal["reduced", "improved", "no_impact", "confirm"]
Severity = Literal["critical", "high", "medium", "low"]
Level = Literal["high", "medium", "low"]
Theme = Literal["continuity", "scope", "limits", "contracts", "market"]

IMPACT_ORDER: tuple[Impact, ...] = ("reduced", "confirm", "no_impact", "improved")
SEVERITY_ORDER: tuple[Severity, ...] = ("critical", "high", "medium", "low")
THEME_ORDER: tuple[Theme, ...] = ("continuity", "scope", "limits", "contracts", "market")


class Badge(BaseModel):
    impact: Impact
    severity: Severity


class Mix(BaseModel):
    reduced: int = 0
    confirm: int = 0
    no_impact: int = 0
    improved: int = 0

    @property
    def total(self) -> int:
        return self.reduced + self.confirm + self.no_impact + self.improved

    def add(self, impact: Impact) -> None:
        setattr(self, impact, getattr(self, impact) + 1)


class PartyCard(BaseModel):
    carrier: str
    policy_number: str
    period: str  # "03/01/2025 – 03/01/2026"


class Cover(BaseModel):
    agency: str
    insured: str
    subtitle: str
    expiring: PartyCard
    renewal: PartyCard
    prepared_line: str


class Kpi(BaseModel):
    label: str
    value: str
    sub: str
    accent: bool = False  # the red "critical changes" tile


class ExecutiveSummary(BaseModel):
    risk: Level
    confidence: Level
    verdict: str  # rich
    confidence_note: str
    kpis: list[Kpi]
    changes_total: int
    mix: Mix


class CriticalChange(BaseModel):
    rank: int
    title: str
    theme: Theme
    description: str
    expiring: str  # rich
    renewal: str  # rich
    badge: Badge
    next_step: str


class Improvement(BaseModel):
    badge: Badge
    text: str  # rich


class ThemeCount(BaseModel):
    theme: Theme
    count: int


class CriticalSection(BaseModel):
    intro: str
    deadline: str
    themes: list[ThemeCount]
    items: list[CriticalChange]
    improvements: list[Improvement] = []
    empty_note: Optional[str] = None


class OverviewRow(BaseModel):
    number: str
    title: str
    takeaway: str
    mix: Mix
    risk: Level


class Row(BaseModel):
    label: str  # rich
    tag: Optional[str] = None  # e.g. "AMENDED MID-TERM"
    sublabel: Optional[str] = None  # grey description under the label
    expiring: str = "—"  # rich
    renewal: str = "—"  # rich
    change: Optional[str] = None  # rich
    badge: Optional[Badge] = None  # None renders a muted "no change" row
    explain_label: str = "Why it matters"
    explain: Optional[str] = None  # rich


class TableBlock(BaseModel):
    kind: Literal["table"] = "table"
    variant: Literal["compare", "forms", "pending"] = "compare"
    heading: Optional[str] = None
    heading_note: Optional[str] = None
    intro: Optional[str] = None
    columns: list[str]
    rows: list[Row]
    footnote: Optional[str] = None  # rich


class QuoteLine(BaseModel):
    side: Literal["expiring", "renewal"]
    text: str
    ref: Optional[str] = None  # "E1 p.5"


class FocusCard(BaseModel):
    topic: str
    badge: Optional[Badge] = None
    mentions: str
    related: Optional[str] = None  # rich
    quotes: list[QuoteLine] = []


class FocusBlock(BaseModel):
    kind: Literal["focus"] = "focus"
    cards: list[FocusCard]


class NoteBlock(BaseModel):
    kind: Literal["note"] = "note"
    text: str  # rich


Block = Annotated[Union[TableBlock, FocusBlock, NoteBlock], Field(discriminator="kind")]


class SectionSummary(BaseModel):
    headline: str
    bullets: list[str] = []
    mix: Mix
    items_compared: int


class RiskAnalysis(BaseModel):
    level: Level
    statement: str
    confidence: Level
    confidence_reason: str


class Section(BaseModel):
    number: str
    title: str
    page_break_before: bool = False
    summary: SectionSummary
    risk: RiskAnalysis
    blocks: list[Block] = []


class SourceDoc(BaseModel):
    ref: str
    document: str
    side: str
    type: str
    pages: int
    how_read: str


class Meta(BaseModel):
    title: str
    brand: str = "Fideon OS"
    agency: str
    header_right: str
    footer_left: str = "Confidential · for discussion; the policies as issued govern coverage"
    disclaimer: str = (
        "This comparison summarises differences between the expiring policy (as amended by its "
        "endorsements and policy changes) and the renewal document, for discussion purposes. It does not "
        "replace the policy wording: coverage is determined solely by the policies as issued."
    )


class Report(BaseModel):
    meta: Meta
    cover: Cover
    executive: ExecutiveSummary
    critical: CriticalSection
    overview: list[OverviewRow]
    sections: list[Section]
    sources: list[SourceDoc]
    audit: dict = {}
