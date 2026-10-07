"""Render a saved report JSON to PDF (and HTML) — used for template fidelity work.

usage: python tools/render_fixture.py tests/fixtures/summit_ridge.report.json output/fixture.pdf [--edition grayscale]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from policy_compare.render.pdf import render_pdf  # noqa: E402
from policy_compare.schema.report import Report  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("report")
ap.add_argument("out")
ap.add_argument("--edition", default="colour", choices=["colour", "grayscale"])
a = ap.parse_args()
rep = Report.model_validate_json(Path(a.report).read_text(encoding="utf-8"))
out = Path(a.out)
print(render_pdf(rep, out, a.edition, html_path=out.with_suffix(".html")))
