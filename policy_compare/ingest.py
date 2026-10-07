"""Load two canonical policy JSONs and decide which is expiring and which is renewal."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from policy_compare.settings import config


class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


class Page(_Loose):
    page_number: int
    text: str = ""
    hidden_text: list = []
    images: list = []


class Document(_Loose):
    sequence: Optional[int] = None
    form_number: Optional[str] = None
    edition: Optional[str] = None
    title: Optional[str] = None
    bookmark_title: Optional[str] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    pages: list[Page] = []


class PolicyPeriod(_Loose):
    effective_date: Optional[str] = None
    expiration_date: Optional[str] = None
    effective_time_basis: Optional[str] = None


class PolicyCore(_Loose):
    policy_number: Optional[str] = None
    policy_type: Optional[str] = None
    policy_period: PolicyPeriod = PolicyPeriod()


class CanonicalPolicy(_Loose):
    """The parts of canonical_insurance_policy the engine relies on. Every other section is allowed and
    compared generically."""
    schema_: Optional[dict] = None
    source_document: dict = {}
    carrier: dict = {}
    policy: PolicyCore = PolicyCore()
    named_insured: dict = {}
    documents: list[Document] = []
    extraction: dict = {}

    model_config = ConfigDict(extra="allow", populate_by_name=True)


@dataclass
class Side:
    """One policy (expiring or renewal) plus everything derived from it."""
    role: str                      # "expiring" | "renewal"
    ref: str                       # "E1" | "R1"
    data: dict                     # raw JSON
    model: CanonicalPolicy
    path: Optional[Path] = None
    warnings: list[str] = field(default_factory=list)

    @property
    def effective(self) -> Optional[date]:
        return parse_date(self.model.policy.policy_period.effective_date)

    @property
    def expiration(self) -> Optional[date]:
        return parse_date(self.model.policy.policy_period.expiration_date)

    @property
    def time_basis(self) -> Optional[time]:
        return parse_time(self.model.policy.policy_period.effective_time_basis)

    @property
    def file_name(self) -> str:
        return self.model.source_document.get("file_name") or (self.path.name if self.path else self.ref)

    @property
    def page_count(self) -> int:
        n = self.model.source_document.get("page_count")
        return int(n) if n else sum(len(d.pages) for d in self.model.documents)

    @property
    def carrier(self) -> str:
        return (self.model.carrier.get("name") or "").strip()

    @property
    def insured(self) -> str:
        return (self.model.named_insured.get("name") or "").strip()


def parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


_TIME = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([AaPp])\.?\s*[Mm]\.?")


def parse_time(value: Any) -> Optional[time]:
    """'12:01 A.M. Standard Time at ...' -> 00:01; 'noon' -> 12:00."""
    if not value:
        return None
    s = str(value)
    if re.search(r"\bnoon\b", s, re.I):
        return time(12, 0)
    m = _TIME.search(s)
    if not m:
        return None
    hour, minute, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
    hour = hour % 12 + (12 if ap == "p" else 0)
    return time(hour, minute)


def load_json(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _validate(raw: dict, label: str) -> tuple[CanonicalPolicy, list[str]]:
    warnings: list[str] = []
    expected = config("catalogue").get("schema", {})
    schema = raw.get("schema") or {}
    if schema.get("name") and schema.get("name") != expected.get("name"):
        warnings.append(f"{label}: schema '{schema.get('name')}' is not '{expected.get('name')}'; comparing generically")
    version = str(schema.get("version") or "")
    if version and version.split(".")[0] != str(expected.get("major_version")):
        warnings.append(f"{label}: schema version {version} differs from supported major {expected.get('major_version')}")
    model = CanonicalPolicy.model_validate({**raw, "schema_": raw.get("schema")})
    if not model.policy.policy_period.effective_date:
        warnings.append(f"{label}: no policy_period.effective_date; expiring/renewal order falls back to input order")
    if not model.documents:
        warnings.append(f"{label}: no documents[] page text; page references and wording checks are unavailable")
    return model, warnings


def load_pair(a: str | Path | dict, b: str | Path | dict) -> tuple[Side, Side, list[str]]:
    """Return (expiring, renewal, warnings). The earlier effective date is the expiring policy."""
    raw_a = a if isinstance(a, dict) else load_json(a)
    raw_b = b if isinstance(b, dict) else load_json(b)
    model_a, warn_a = _validate(raw_a, "input A")
    model_b, warn_b = _validate(raw_b, "input B")
    warnings = warn_a + warn_b
    ea, eb = parse_date(model_a.policy.policy_period.effective_date), parse_date(model_b.policy.policy_period.effective_date)
    swap = bool(ea and eb and eb < ea)
    if ea and eb and ea == eb:
        warnings.append("both policies have the same effective date; treating input A as expiring")
    (exp_raw, exp_model, exp_src), (ren_raw, ren_model, ren_src) = (
        ((raw_b, model_b, b), (raw_a, model_a, a)) if swap else ((raw_a, model_a, a), (raw_b, model_b, b))
    )
    expiring = Side("expiring", "E1", exp_raw, exp_model, Path(exp_src) if not isinstance(exp_src, dict) else None)
    renewal = Side("renewal", "R1", ren_raw, ren_model, Path(ren_src) if not isinstance(ren_src, dict) else None)
    return expiring, renewal, warnings


def continuity(expiring: Side, renewal: Side) -> Optional[timedelta]:
    """Renewal inception minus expiring expiry (positive = gap, negative = overlap)."""
    if not (expiring.expiration and renewal.effective):
        return None
    t_exp = expiring.time_basis or time(0, 1)
    t_ren = renewal.time_basis or time(0, 1)
    return datetime.combine(renewal.effective, t_ren) - datetime.combine(expiring.expiration, t_exp)
