"""F: read scanned pages with the vision model (Qwen3-VL).

A page counts as scanned when its text layer is (almost) empty. If the original PDF is available (passed in, or found
in SOURCE_PDF_DIR by file name or sha256) the page is rendered and the model transcribes it; the transcription becomes
that page's text, so values, quotes and wording checks work on it like on any digital page.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Iterable, Optional

from policy_compare.ingest import Side
from policy_compare.settings import settings

log = logging.getLogger("policy_compare")
INSTRUCTION = ("This is one page of an insurance policy. Transcribe all text on the page exactly as printed, in reading "
               "order, keeping line breaks and table rows on separate lines. Output only the text.")
MIN_TEXT = 20


def scanned_pages(side: Side) -> list:
    return [p for d in side.model.documents for p in d.pages if len((p.text or "").strip()) < MIN_TEXT]


def find_pdf(side: Side, candidates: Iterable[Path] = (), folder: Optional[Path] = None) -> Optional[Path]:
    """The source PDF of one policy: same file name, or same sha256 as recorded in source_document."""
    want_name = (side.model.source_document.get("file_name") or "").lower()
    want_sha = (side.model.source_document.get("sha256") or "").lower()
    pool = [Path(c) for c in candidates if c]
    if folder and Path(folder).is_dir():
        pool += sorted(Path(folder).glob("*.pdf")) + sorted(Path(folder).glob("*.PDF"))
    for p in pool:
        if p.name.lower() == want_name:
            return p
    if want_sha:
        for p in pool:
            if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == want_sha:
                return p
    return None


def ocr_side(llm, side: Side, pdf: Path, warnings: list[str]) -> list[int]:
    """Transcribe the scanned pages of one policy in place. Returns the page numbers read."""
    import pymupdf

    pages = scanned_pages(side)[: settings().ocr_max_pages]
    if not pages:
        return []
    done = []
    with pymupdf.open(pdf) as doc:
        for p in pages:
            if not 1 <= p.page_number <= doc.page_count:
                continue
            png = doc[p.page_number - 1].get_pixmap(dpi=150).tobytes("png")
            try:
                text = llm.vision_text("ocr", INSTRUCTION, png)
            except Exception as e:  # keep going: an unread page stays blank and is reported
                warnings.append(f"{side.ref} p.{p.page_number}: scanned page could not be read ({e})")
                continue
            if len(text.strip()) >= MIN_TEXT:
                p.text = text
                done.append(p.page_number)
    side.ocr_pages = done
    return done


def run_ocr(llm, sides: list[Side], pdfs: Iterable[Path], warnings: list[str]) -> dict[str, list[int]]:
    out = {}
    pdfs = list(pdfs)
    for side in sides:
        if not scanned_pages(side):
            continue
        pdf = find_pdf(side, pdfs, settings().source_pdf_dir)
        if not pdf:
            warnings.append(f"{side.ref}: {len(scanned_pages(side))} scanned page(s) but the source PDF was not provided; "
                            "they stay unread (pass --pdf or set SOURCE_PDF_DIR)")
            continue
        out[side.ref] = ocr_side(llm, side, pdf, warnings)
    return out
