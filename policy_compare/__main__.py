"""CLI: python -m policy_compare --a policy1.json --b policy2.json   (report saved in the output folder)

    --ask "Is snow removal covered?"   answer a question from the two policies instead of building the report
    --pdf a.pdf --pdf b.pdf            original policy PDFs, used to read scanned pages with a vision model
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="policy_compare", description="Policy renewal comparison report (Qwen3-VL on RunPod)")
    ap.add_argument("--a", help="first canonical policy JSON (order does not matter)")
    ap.add_argument("--b", help="second canonical policy JSON")
    ap.add_argument("--out", help="PDF file name or path (default: output/<Insured>_renewal_comparison_<date>.pdf; "
                                  "a bare file name is also saved in output/)")
    ap.add_argument("--focus", default="", help='client focus topics, comma separated, e.g. "silica, additional insured"')
    ap.add_argument("--edition", default="colour", choices=["colour", "grayscale"])
    ap.add_argument("--no-llm", action="store_true", help="skip the model; deterministic narrative only")
    ap.add_argument("--from-report", help="re-render a saved report JSON without re-running the comparison")
    ap.add_argument("--html", action="store_true", help="also write the HTML next to the PDF")
    ap.add_argument("--ask", help="answer one question from the two policies (with verified quotes) instead of a report")
    ap.add_argument("--pdf", action="append", default=[], help="original policy PDF for reading scanned pages (repeatable)")
    ap.add_argument("--source-dir", help="folder with the original policy PDFs (matched by file name or sha256)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    sys.stdout.reconfigure(encoding="utf-8")

    if args.source_dir:
        from policy_compare.settings import settings
        settings().source_pdf_dir = Path(args.source_dir)
    if args.ask:
        if not (args.a and args.b):
            ap.error("--ask needs --a and --b")
        return _ask(args)

    from policy_compare.output import report_name, resolve
    from policy_compare.render.pdf import render_pdf
    from policy_compare.schema.report import Report

    if args.from_report:
        report = Report.model_validate_json(Path(args.from_report).read_text(encoding="utf-8"))
        out = resolve(args.out, report_name(report.cover.insured, report.cover.renewal.period, args.edition))
    else:
        if not (args.a and args.b):
            ap.error("--a and --b are required unless --from-report is given")
        from policy_compare.engine import analyse
        from policy_compare.report.build import build_report

        focus = [t for t in args.focus.split(",") if t.strip()]
        an = analyse(args.a, args.b, focus=focus, use_llm=not args.no_llm, pdfs=[Path(p) for p in args.pdf])
        report = build_report(an)
        out = resolve(args.out, report_name(report.cover.insured, report.cover.renewal.period, args.edition))
        rj = out.with_name(out.stem.removesuffix("_grayscale") + ".report.json")
        rj.parent.mkdir(parents=True, exist_ok=True)
        rj.write_text(report.model_dump_json(indent=1), encoding="utf-8")
        print(f"expiring : {an.E.file_name}  ({an.E.effective})")
        print(f"renewal  : {an.R.file_name}  ({an.R.effective})")
        print(f"changes  : {report.executive.changes_total}  critical: {len(report.critical.items)}  risk: {report.executive.risk}")
        print(f"model    : {'Qwen via ' + an.audit['model'] if an.audit.get('llm') else 'off (deterministic narrative)'}")
        for w in an.warnings:
            print(f"warning  : {w}")
        print(f"report   : {rj}")
        if an.drafts:
            from policy_compare.analysis.drafts import write_drafts
            for p in write_drafts(an.drafts, out.with_name(out.stem.removesuffix("_grayscale"))):
                print(f"draft    : {p}")
    pdf = render_pdf(report, out, args.edition, html_path=out.with_suffix(".html") if args.html else None)
    print(f"pdf      : {pdf}")
    return 0


def _ask(args) -> int:
    from policy_compare.qa import ask

    res = ask(args.a, args.b, args.ask, use_llm=not args.no_llm)
    print(f"question : {res['question']}")
    if res.get("answer"):
        print(f"answer   : {res['answer']}")
        print(f"verified : {'yes - every citation found word for word' if res.get('verified') else 'no'}"
              + (f"  (confidence {res['confidence']})" if res.get("confidence") else ""))
    else:
        print(f"answer   : none ({res.get('mode')})")
    for c in res.get("citations", []):
        print(f"cites    : [{c['ref']}] “{c['quote']}”")
    if not res.get("verified"):
        for p in res.get("passages", []):
            print(f"passage  : [{p['ref']}] {p['text']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
