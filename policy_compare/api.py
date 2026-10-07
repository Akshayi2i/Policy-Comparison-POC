"""HTTP API.

    uvicorn policy_compare.api:app --port 8000

    POST /compare   multipart: policy_a, policy_b (canonical JSON files), focus (optional, comma separated),
                    edition (colour | grayscale), llm (true | false), format (pdf | json),
                    pdf_a / pdf_b (optional original PDFs, used to read scanned pages with a vision model)
    POST /ask       multipart: policy_a, policy_b, question -> answer with verified quotes and page references
    GET  /health    service + model endpoint status

The report JSON (format=json) carries the carrier email and client letter drafts under "drafts".
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from policy_compare.settings import settings

app = FastAPI(title="Policy Comparison Engine", version="0.1.0",
              description="Two canonical policy JSONs in, a Fideon OS renewal comparison PDF out (Qwen3-VL on RunPod).")


@app.get("/health")
def health() -> dict:
    s = settings()
    out = {"status": "ok", "llm": {"configured": s.llm_enabled, "model": s.llm_model if s.llm_enabled else None}}
    if s.llm_enabled:
        try:
            from policy_compare.llm.client import LLMClient
            out["llm"]["served_models"] = LLMClient().ping()
            out["llm"]["reachable"] = True
        except Exception as e:  # report, do not fail the health check
            out["llm"].update({"reachable": False, "error": str(e)[:300]})
    return out


def _read_json(upload: UploadFile, name: str) -> dict:
    try:
        return json.loads(upload.file.read())
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"{name} is not valid JSON: {e}") from e


def _save_pdfs(folder: str, uploads: list[Optional[UploadFile]]) -> list[Path]:
    """Keep the uploaded PDFs under their own file names (scanned pages are matched by name or sha256)."""
    out = []
    for up in uploads:
        if up is None or not up.filename:
            continue
        path = Path(folder) / Path(up.filename).name
        path.write_bytes(up.file.read())
        out.append(path)
    return out


@app.post("/compare")
def compare(policy_a: UploadFile = File(...), policy_b: UploadFile = File(...), focus: str = Form(""),
            edition: Literal["colour", "grayscale"] = Form("colour"), llm: bool = Form(True),
            format: Literal["pdf", "json"] = Form("pdf"),
            pdf_a: Optional[UploadFile] = File(None), pdf_b: Optional[UploadFile] = File(None)):
    # sync endpoint: FastAPI runs it in a worker thread, which the sync Playwright API requires
    from policy_compare.engine import analyse
    from policy_compare.render.pdf import render_pdf
    from policy_compare.report.build import build_report

    a, b = _read_json(policy_a, "policy_a"), _read_json(policy_b, "policy_b")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            an = analyse(a, b, focus=[t for t in focus.split(",") if t.strip()], use_llm=llm,
                         pdfs=_save_pdfs(tmp, [pdf_a, pdf_b]))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"comparison failed: {e}") from e
    report = build_report(an)
    # every report is also kept in the output folder (PDF + report JSON)
    from policy_compare.output import report_name, resolve
    out = resolve(None, report_name(report.cover.insured, report.cover.renewal.period, edition))
    rj = out.with_name(out.stem.removesuffix("_grayscale") + ".report.json")
    rj.write_text(report.model_dump_json(indent=1), encoding="utf-8")
    if an.drafts:
        from policy_compare.analysis.drafts import write_drafts
        write_drafts(an.drafts, out.with_name(out.stem.removesuffix("_grayscale")))
    if format == "json":
        return JSONResponse(json.loads(report.model_dump_json()), headers={"X-Saved-To": str(rj)})
    render_pdf(report, out, edition)
    return FileResponse(out, media_type="application/pdf", filename=out.name,
                        headers={"X-Changes-Found": str(report.executive.changes_total),
                                 "X-Critical-Changes": str(len(report.critical.items)),
                                 "X-Overall-Risk": report.executive.risk,
                                 "X-Model-Used": "true" if an.audit.get("llm") else "false",
                                 "X-Saved-To": str(out)})


@app.post("/ask")
def ask_question(policy_a: UploadFile = File(...), policy_b: UploadFile = File(...), question: str = Form(...),
                 llm: bool = Form(True)) -> dict:
    """Answer a question from the two policies. The answer is returned only with quotes found word for word in the
    policy text (with page references); otherwise the best matching passages are returned instead."""
    from policy_compare.qa import ask

    if not question.strip():
        raise HTTPException(status_code=422, detail="question is empty")
    a, b = _read_json(policy_a, "policy_a"), _read_json(policy_b, "policy_b")
    try:
        return ask(a, b, question.strip(), use_llm=llm)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"question failed: {e}") from e
