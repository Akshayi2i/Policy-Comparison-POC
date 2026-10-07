"""The Summit Ridge fixture is a transcription of the reference PDF; its numbers must match the reference."""
import json
from pathlib import Path

from policy_compare.render.pdf import render_html
from policy_compare.schema.report import Report

FIXTURE = Path(__file__).parent / "fixtures" / "summit_ridge.report.json"


def load() -> Report:
    return Report.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_counts_match_reference():
    r = load()
    assert [s.summary.items_compared for s in r.sections] == [7, 3, 7, 6, 9, 2, 7, 3, 2]
    assert [s.summary.mix.total for s in r.sections] == [7, 3, 7, 6, 9, 2, 7, 3, 2]
    assert r.executive.kpis[2].value == "+1 / −5"
    crit = r.critical.items
    assert len(crit) == 10
    assert [c.badge.severity for c in crit].count("critical") == 8
    assert [c.badge.severity for c in crit].count("high") == 2
    assert [t.count for t in r.critical.themes] == [2, 2, 2, 3, 1]


def test_fixture_renders_both_editions():
    r = load()
    colour, gray = render_html(r, "colour"), render_html(r, "grayscale")
    assert "Summit Ridge Builders Inc" in colour and "Grayscale key" not in colour
    assert "Grayscale key" in gray and "each category" in gray
