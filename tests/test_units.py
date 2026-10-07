from datetime import date, time, timedelta

from policy_compare.analysis.guards import ceiling_risk, clean_text, unsupported_numbers
from policy_compare.findings import Finding
from policy_compare.flatten import Flattener, to_cell
from policy_compare.fmt import duration, money, money_compact, pct
from policy_compare.ingest import parse_date, parse_time
from policy_compare.llm.client import _extract_json, inline_refs
from policy_compare.render.pdf import rich
from policy_compare.schema.llm_io import Synthesis
from policy_compare.textindex import edition_key, form_key


def test_money_and_percent():
    assert money(1879.0) == "$1,879"
    assert money(2150.5) == "$2,150.50"
    assert pct(-3500, 31400) == "−11.1%"
    assert pct(-1_000_000, 2_000_000) == "−50%"
    assert pct(5000, 5000) == "+100%"
    assert money_compact(2_000_000) == "$2M"
    assert money_compact(1_800_000) == "$1.8M"
    assert money_compact(400_000) == "$400K"
    assert money_compact(5_000) == "$5,000"


def test_duration_and_time_basis():
    assert duration(timedelta(hours=11, minutes=59)) == "11 h 59 min"
    assert parse_time("12:01 A.M. Standard Time at Location of Designated Premises.") == time(0, 1)
    assert parse_time("12:00 P.M. Standard Time") == time(12, 0)
    assert parse_time("noon") == time(12, 0)
    assert parse_date("2026-09-24") == date(2026, 9, 24)
    assert parse_date("09/24/2026") == date(2026, 9, 24)


def test_form_number_and_edition_normalization():
    assert form_key("CG 20 37") == form_key("cg2037") == form_key("CG 20-37") == "CG2037"
    assert edition_key("04 23") == edition_key("04-23") == edition_key("0423") == "04/23"
    assert edition_key("1.2") == "1.2"


def test_value_cells():
    c = to_cell({"label": "Each Occurrence Limit", "text": "$1,000,000", "amount": 1000000, "kind": "amount"})
    assert c.kind == "amount" and c.amount == 1_000_000 and c.compare_key == "num:1e+06"
    assert to_cell({"text": "$None", "amount": None, "kind": "none"}).text == "None"
    assert not to_cell({"text": None, "amount": None, "kind": "not_stated"}).present
    assert to_cell("2026-09-24").text == "09/24/2026"


def test_identity_alignment_ignores_order():
    e = {"forms": {"list": [{"form_number": "A 1", "edition": "01 20"}, {"form_number": "B 2", "edition": "02 20"}]}}
    r = {"forms": {"list": [{"form_number": "B 2", "edition": "02 20"}, {"form_number": "A 1", "edition": "01 20"}]}}
    fl = Flattener(e, r)
    fe, fr = fl.flatten(e), fl.flatten(r)
    assert set(fe) == set(fr)
    assert all(fe[k].cell.compare_key == fr[k].cell.compare_key for k in fe)


def test_text_guard_rejects_invented_numbers():
    allowed = {"1000000", "500000", "50", "09/24/2026"}
    assert clean_text("The limit fell to $500,000.", 20, allowed) == "The limit fell to $500,000."
    assert clean_text("The limit fell to $750,000.", 20, allowed) is None
    assert unsupported_numbers("Premium rose 12%.", allowed) == ["12%"]
    assert clean_text("**Bold** headline", 10) == "Bold headline"
    assert clean_text("word " * 50, 10) is None


def test_risk_ceiling():
    admin = Finding(id="f1", change_key="a", section="policy", kind="value", label="Effective Date", impact="no_impact", severity="low")
    conf = Finding(id="f2", change_key="b", section="forms", kind="form_removed", label="X", impact="confirm", severity="medium")
    red = Finding(id="f3", change_key="c", section="limits", kind="value", label="Y", impact="reduced", severity="high")
    assert ceiling_risk([admin]) == "low"
    assert ceiling_risk([admin, conf]) == "medium"
    assert ceiling_risk([conf, red]) == "high"


def test_inline_refs_keeps_property_named_title():
    schema = inline_refs(Synthesis.model_json_schema())
    crit = schema["properties"]["critical"]["items"]
    assert "title" in crit["properties"] and "title" in crit["required"]
    assert "$defs" not in schema and "$ref" not in str(schema)


def test_extract_json_strips_fences():
    assert _extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _extract_json('Sure: {"a": 1}') == '{"a": 1}'


def test_rich_markup_escapes_and_chips():
    out = str(rich("**Bold** <b>x</b> [[E1 p.6]]"))
    assert "<strong>Bold</strong>" in out and "&lt;b&gt;" in out and '<span class="ref">E1 p.6</span>' in out
