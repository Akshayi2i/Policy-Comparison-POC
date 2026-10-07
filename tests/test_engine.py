"""End-to-end engine checks on the real Halstead pair and on synthetic scenarios (no model)."""
import json
from datetime import date

import pytest

from tests.conftest import POLICY_1, POLICY_2
from make_scenarios import SCENARIOS, build
from policy_compare.engine import analyse
from policy_compare.report.build import build_report


@pytest.fixture(scope="module")
def halstead():
    return analyse(POLICY_1, POLICY_2, use_llm=False)


def test_expiring_is_the_earlier_policy(halstead):
    assert halstead.E.effective == date(2025, 9, 24)
    assert halstead.R.effective == date(2026, 9, 24)


def test_input_order_does_not_matter():
    a = build_report(analyse(POLICY_1, POLICY_2, use_llm=False)).model_dump(exclude={"audit"})
    b = build_report(analyse(POLICY_2, POLICY_1, use_llm=False)).model_dump(exclude={"audit"})
    assert a == b


def test_halstead_truth(halstead):
    forms = halstead.findings["forms"]
    removed = sorted(f.context["form_number"] for f in forms if f.kind == "form_removed")
    assert removed == ["CP 382", "DNCANY"]
    assert not [f for f in forms if f.kind in ("form_added", "form_edition", "form_wording")]
    assert halstead.premium() == (1879.0, 1879.0)
    assert halstead.R.insured == "DEREK P HALSTEAD CONSTRUCTION CO INC"
    cont = next(f for f in halstead.findings["policy"] if f.kind == "continuity")
    assert cont.change == "Continuous (no gap)" and not cont.changed
    limits_changed = [f for f in halstead.findings["limits"] if f.changed]
    assert limits_changed == []
    assert halstead.critical_candidates() == []


def test_changed_values_carry_page_references(halstead):
    for f in halstead.all_findings():
        if f.changed and f.section in ("policy", "premium", "limits", "terms", "forms"):
            if f.exp not in ("—", "", "Not on policy"):
                assert f.exp_ref, f.label
            if f.ren not in ("—", "", "Not on policy"):
                assert f.ren_ref, f.label


def _find(an, section, pred):
    return [f for f in an.findings[section] if pred(f)]


EXPECT = {
    "limit_cut": lambda an: _find(an, "limits", lambda f: f.label == "Each Occurrence Limit" and f.impact == "reduced"
                                  and f.severity == "critical" and f.change == "Decreased $500,000 (−50%)"),
    "slot_form_removed": lambda an: _find(an, "forms", lambda f: f.kind == "form_removed" and f.context["form_number"] == "BAI 1"
                                          and f.impact == "reduced" and f.severity == "critical")
                                    and _find(an, "checklist", lambda f: "ongoing" in f.label and f.change == "Lost on renewal"),
    "exclusion_added": lambda an: _find(an, "forms", lambda f: f.kind == "form_added" and f.impact == "reduced" and f.severity == "high"),
    "edition_bump": lambda an: _find(an, "forms", lambda f: f.kind == "form_edition" and f.change == "Edition 11/05 → 01/26"),
    "premium_up": lambda an: build_report(an).executive.kpis[0].value == "+$271.50",
    "carrier_change": lambda an: _find(an, "policy", lambda f: f.label == "Insurer" and f.impact == "confirm")
                                 and _find(an, "checklist", lambda f: f.label == "Carrier change" and f.change == "New carrier"),
    "time_gap": lambda an: _find(an, "policy", lambda f: f.kind == "continuity" and f.change == "Gap of 11 h 59 min"
                                 and f.severity == "critical"),
    "deductible_introduced": lambda an: _find(an, "premium", lambda f: f.label == "Property Damage Deductible"
                                              and f.impact == "reduced" and f.change == "None → $5,000"),
    "surplus_lines": lambda an: _find(an, "policy", lambda f: f.kind == "admitted" and f.change == "Now non-admitted"),
    "wording_change": lambda an: _find(an, "forms", lambda f: f.kind == "form_wording" and "“" in f.change),
    "unknown_section": lambda an: _find(an, "limits", lambda f: f.label == "Cyber Aggregate Limit" and f.severity == "critical")
                                  and _find(an, "terms", lambda f: f.label == "Waiting period" and f.changed),
    "midterm_document": lambda an: an.findings["midterm"],
}


@pytest.mark.parametrize("name", sorted(EXPECT))
def test_scenario(name):
    e, r = build(name)
    an = analyse(e, r, use_llm=False)
    assert EXPECT[name](an), name


def test_reorder_creates_no_false_differences(halstead):
    e, r = build("reorder_only")
    an = analyse(e, r, use_llm=False)
    assert sorted(f.change_key for f in an.unique_changes()) == sorted(f.change_key for f in halstead.unique_changes())


def test_cross_reference_rows_are_not_double_counted():
    e, r = build("slot_form_removed")
    an = analyse(e, r, use_llm=False)
    rep = build_report(an)
    keys = [f.change_key for f in an.unique_changes()]
    assert len(keys) == len(set(keys))
    assert rep.executive.changes_total == len(keys)
    # the checklist row repeats the form removal: counted in its section, once in the executive total
    assert rep.sections[6].summary.items_compared >= 1


def test_combined_scenario_report_is_complete():
    e, r = build("combined")
    rep = build_report(analyse(e, r, focus=["additional insured", "silica"], use_llm=False))
    assert 1 <= len(rep.critical.items) <= 10
    assert rep.executive.risk == "high"
    assert [c.rank for c in rep.critical.items] == list(range(1, len(rep.critical.items) + 1))
    sev = [c.badge.severity for c in rep.critical.items]
    assert sev == sorted(sev, key=["critical", "high"].index)
    json.loads(rep.model_dump_json())
