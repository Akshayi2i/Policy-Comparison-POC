"""Synthetic test scenarios derived from a real pair of canonical policies.

Each scenario applies one kind of change to the renewal (or expiring) JSON so the engine's handling can be
checked without new source documents. `combined` stacks many changes to exercise every part of the report.

usage: python tools/make_scenarios.py [--out tests/scenarios]
"""
from __future__ import annotations

import argparse
import copy
import json
import random
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
BASE_EXPIRING = ROOT / "canonical_json" / "compare_policy_2.canonical.json"   # 2025-26
BASE_RENEWAL = ROOT / "canonical_json" / "compare_policy_1.canonical.json"    # 2026-27


def load_base() -> tuple[dict, dict]:
    return (json.loads(BASE_EXPIRING.read_text(encoding="utf-8")), json.loads(BASE_RENEWAL.read_text(encoding="utf-8")))


def _cell(amount, text=None, label=None):
    c = {"text": text or f"${amount:,.0f}", "amount": amount, "kind": "amount"}
    return {"label": label, **c} if label else c


def _decl_page(doc_json: dict, page_no: int) -> dict:
    for d in doc_json["documents"]:
        for p in d["pages"]:
            if p["page_number"] == page_no:
                return p
    raise KeyError(page_no)


def _replace_text(doc_json: dict, old: str, new: str) -> None:
    for d in doc_json["documents"]:
        for p in d["pages"]:
            p["text"] = p["text"].replace(old, new)


def _remove_form(doc_json: dict, number: str) -> None:
    inv = doc_json["forms_inventory"]["forms"]
    doc_json["forms_inventory"]["forms"] = [f for f in inv if f["form_number"] != number]
    doc_json["documents"] = [d for d in doc_json["documents"] if d.get("form_number") != number]
    _replace_text(doc_json, f"\n{number}  ", "\n")


def _add_form(doc_json: dict, number: str, edition: str, title: str, text: str) -> None:
    doc_json["forms_inventory"]["forms"].append({"sequence": 99, "form_number": number, "edition": edition, "title": title,
                                                 "attached_in_document": True, "document_sequence": 99, "edition_matches_attached_form": True})
    last = max(p["page_number"] for d in doc_json["documents"] for p in d["pages"])
    doc_json["documents"].append({"sequence": 99, "bookmark_title": f"99. {number} {title}", "form_number": number, "edition": edition,
                                  "title": title, "listed_in_forms_inventory": True, "page_start": last + 1, "page_end": last + 1,
                                  "page_count": 1, "pages": [{"page_number": last + 1, "layout": "single_column", "text": text,
                                                              "hidden_text": [], "images": [], "checkboxes": []}]})
    _decl_page(doc_json, 6)["text"] += f"\n{number}  {edition}  {title}"


# ---------------- scenarios: (expiring, renewal) -> None, mutating in place ----------------

def limit_cut(e, r):
    r["general_liability"]["limits"]["each_occurrence"] = _cell(500000, label="Each Occurrence Limit")
    _replace_text(r, "Each Occurrence Limit  $1,000,000", "Each Occurrence Limit  $500,000")


def slot_form_removed(e, r):
    _remove_form(r, "BAI 1")


def exclusion_added(e, r):
    _add_form(r, "XMOLD 1", "01 26", "Exclusion - Fungi or Bacteria (Mold)",
              "EXCLUSION - FUNGI OR BACTERIA. This insurance does not apply to bodily injury or property damage arising out of mold.")


def edition_bump(e, r):
    for f in r["forms_inventory"]["forms"]:
        if f["form_number"] == "AP 0230UF":
            f["edition"] = "01 26"
    _replace_text(r, "AP 0230UF  11 05", "AP 0230UF  01 26")


def premium_up(e, r):
    r["premium"]["annual_premium"] = {"label": "ANNUAL PREMIUM", "text": "$2,150.50", "amount": 2150.5, "kind": "amount"}
    _replace_text(r, "ANNUAL PREMIUM: $1,879.00", "ANNUAL PREMIUM: $2,150.50")


def carrier_change(e, r):
    r["carrier"]["name"] = "GRANITE SPECIALTY INSURANCE COMPANY"
    _replace_text(r, "UTICA FIRST INSURANCE COMPANY\nCONSTITUTED", "GRANITE SPECIALTY INSURANCE COMPANY\nCONSTITUTED")


def time_gap(e, r):
    r["policy"]["policy_period"]["effective_time_basis"] = "12:00 P.M. Standard Time at Location of Designated Premises."


def deductible_introduced(e, r):
    r["general_liability"]["property_damage_deductible"] = {"label": "Property Damage Deductible", "text": "$5,000", "amount": 5000, "kind": "amount"}
    _replace_text(r, "Property Damage Deductible  $None", "Property Damage Deductible  $5,000")


def surplus_lines(e, r):
    _decl_page(r, 3)["text"] += "\nThis insurance is placed with a non-admitted insurer under the surplus lines law of New York."


def wording_change(e, r):
    _replace_text(r, "bodily injury", "bodily injury, property damage or personal and advertising injury")


def unknown_section(e, r):
    e["cyber_liability"] = {"section_title": "CYBER LIABILITY", "limits": {"aggregate": _cell(250000, label="Cyber Aggregate Limit")},
                            "waiting_period": "12 hours"}
    r["cyber_liability"] = {"section_title": "CYBER LIABILITY", "limits": {"aggregate": _cell(100000, label="Cyber Aggregate Limit")},
                            "waiting_period": "24 hours"}


def midterm_document(e, r):
    last = max(p["page_number"] for d in e["documents"] for p in d["pages"])
    e["documents"].append({"sequence": 98, "bookmark_title": "98. PCE Policy Change Endorsement No. 2", "form_number": "PCE",
                           "edition": "09 25", "title": "Policy Change Endorsement No. 2", "listed_in_forms_inventory": False,
                           "page_start": last + 1, "page_end": last + 1, "page_count": 1,
                           "pages": [{"page_number": last + 1, "layout": "single_column", "hidden_text": [], "images": [], "checkboxes": [],
                                      "text": "POLICY CHANGE ENDORSEMENT NO. 2\nEffective 01/15/2026\nThe Each Occurrence Limit is changed from $1,000,000 to $2,000,000."}]})


def reorder_only(e, r):
    rnd = random.Random(7)
    rnd.shuffle(r["forms_inventory"]["forms"])
    rnd.shuffle(r["package_endorsement"]["coverages"])
    rnd.shuffle(r["optional_liability_coverage_list"]["coverages"])


def combined(e, r):
    for fn in (limit_cut, slot_form_removed, exclusion_added, premium_up, carrier_change, time_gap, deductible_introduced,
               surplus_lines, edition_bump):
        fn(e, r)
    _remove_form(r, "PNCAI")
    r["general_liability"]["limits"]["medical_payments"] = _cell(10000, label="Medical Payments Limit")


SCENARIOS: dict[str, Callable] = {f.__name__: f for f in (
    limit_cut, slot_form_removed, exclusion_added, edition_bump, premium_up, carrier_change, time_gap, deductible_introduced,
    surplus_lines, wording_change, unknown_section, midterm_document, reorder_only, combined)}


def build(name: str) -> tuple[dict, dict]:
    e, r = load_base()
    e, r = copy.deepcopy(e), copy.deepcopy(r)
    SCENARIOS[name](e, r)
    return e, r


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "tests" / "scenarios"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name in SCENARIOS:
        e, r = build(name)
        (out / f"{name}.expiring.json").write_text(json.dumps(e, ensure_ascii=False), encoding="utf-8")
        (out / f"{name}.renewal.json").write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
        print(out / name)
