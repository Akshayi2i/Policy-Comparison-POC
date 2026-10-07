"""Output contracts for each model task. Schemas stay simple (types + enums) so every vLLM guided-decoding
backend accepts them; lengths and list sizes are enforced by the guards afterwards."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

Impact = Literal["reduced", "improved", "no_impact", "confirm"]
Severity = Literal["critical", "high", "medium", "low"]
Level = Literal["high", "medium", "low"]
Theme = Literal["continuity", "scope", "limits", "contracts", "market"]
SectionName = Literal["policy", "premium", "limits", "terms", "ignore"]
ElementType = Literal["limit", "deductible", "premium", "date", "identifier", "party", "text", "term"]


class ItemAssessment(BaseModel):
    id: str
    impact: Impact
    severity: Severity
    why: str


class SectionText(BaseModel):
    headline: str
    bullets: list[str]
    takeaway: str
    risk_level: Level
    risk_statement: str
    confidence: Level
    confidence_reason: str


class SectionAssessment(SectionText):
    items: list[ItemAssessment]


class PathClass(BaseModel):
    pattern: str
    section: SectionName
    type: ElementType
    label: str


class PathClassification(BaseModel):
    items: list[PathClass]


class TopicPick(BaseModel):
    topics: list[str]


class CriticalItem(BaseModel):
    id: str
    title: str
    theme: Theme
    description: str
    next_step: str


class QuotePick(BaseModel):
    topic: str
    expiring: list[int]
    renewal: list[int]


class Synthesis(BaseModel):
    risk: Level
    confidence: Level
    lead: str
    body: str
    confidence_note: str
    critical: list[CriticalItem]
    checklist: SectionText
    focus: SectionText
    pending: SectionText
    focus_quotes: list[QuotePick]


class MidtermChange(BaseModel):
    endorsement: str
    effective_date: Optional[str]
    item: str
    before: Optional[str]
    after: str
    quote: str


class MidtermExtraction(BaseModel):
    changes: list[MidtermChange]
