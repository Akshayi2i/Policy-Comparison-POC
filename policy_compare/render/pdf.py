"""Render a Report to HTML (Jinja2) and to PDF (Playwright Chromium)."""
from __future__ import annotations

import base64
import html
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

from policy_compare.schema.report import Report

HERE = Path(__file__).parent
STATIC = HERE / "static"
Edition = Literal["colour", "grayscale"]
PAGE_MARGINS_PT = {"top": 45.5, "bottom": 46, "left": 39.7, "right": 39.7}

_REF = re.compile(r"\[\[(.+?)\]\]")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])")


def rich(value: object) -> Markup:
    """Escape text, then apply the report mini-markup: **bold**, *italic*, [[E1 p.6]] chips."""
    if value is None:
        return Markup("")
    text = html.escape(str(value), quote=False)
    text = _REF.sub(r'<span class="ref">\1</span>', text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    return Markup(text)


@lru_cache(maxsize=None)
def _font_uri(name: str) -> str:
    data = base64.b64encode((STATIC / "fonts" / name).read_bytes()).decode()
    return f"data:font/woff2;base64,{data}"


@lru_cache(maxsize=None)
def _css(edition: str) -> str:
    css = (STATIC / "report.css").read_text(encoding="utf-8")
    if edition == "grayscale":
        css += "\n" + (STATIC / "grayscale.css").read_text(encoding="utf-8")
    return css.replace("{{FONT_REGULAR}}", _font_uri("InterVariable.woff2")).replace(
        "{{FONT_ITALIC}}", _font_uri("InterVariable-Italic.woff2")
    )


@lru_cache(maxsize=None)
def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(HERE / "templates"),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["rich"] = rich
    return env


def render_html(report: Report, edition: Edition = "colour") -> str:
    return _env().get_template("report.html.j2").render(r=report, edition=edition, css=Markup(_css(edition)))


FONT_CACHE = STATIC / "fonts" / "_static"
HEADER_SIZE = 5.2
HEADER_BASELINE = 20.6  # pt from top; matches the reference header text box (top 15.5pt)
FOOTER_BASELINE = 775.8
LEFT_X, RIGHT_X = 39.7, 612 - 39.7


def _hex(c: str) -> tuple[float, float, float]:
    return tuple(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))


@lru_cache(maxsize=None)
def _static_inter(weight: int) -> str:
    """A static Inter TTF instanced from the variable font (PyMuPDF cannot embed woff2/variable fonts)."""
    path = FONT_CACHE / f"Inter-{weight}.ttf"
    if not path.exists():
        from fontTools.ttLib import TTFont
        from fontTools.varLib import instancer

        FONT_CACHE.mkdir(parents=True, exist_ok=True)
        font = instancer.instantiateVariableFont(TTFont(str(STATIC / "fonts" / "InterVariable.woff2")), {"wght": weight})
        font.flavor = None
        font.save(str(path))
    return str(path)


def _stamp_running(pdf_path: Path, report: Report, edition: Edition) -> None:
    """Stamp the running header and footer onto every page.

    Chromium drops header/footer templates that load a web font, so they are drawn here with Inter instead.
    """
    import pymupdf

    regular, bold = pymupdf.Font(fontfile=_static_inter(400)), pymupdf.Font(fontfile=_static_inter(700))
    navy, grey = _hex("#14284b"), _hex("#5d6675")
    footer_left = report.meta.footer_left + (" · Grayscale print edition" if edition == "grayscale" else "")
    doc = pymupdf.open(pdf_path)
    total = doc.page_count
    for i, page in enumerate(doc):
        tw = pymupdf.TextWriter(page.rect)
        brand = report.meta.brand
        tw.append((LEFT_X, HEADER_BASELINE), brand, font=bold, fontsize=HEADER_SIZE)
        page.write_text(writers=[tw], color=navy)
        tw = pymupdf.TextWriter(page.rect)
        tw.append((LEFT_X + bold.text_length(brand, HEADER_SIZE), HEADER_BASELINE), f" · {report.meta.agency}", font=regular, fontsize=HEADER_SIZE)
        right = report.meta.header_right
        tw.append((RIGHT_X - regular.text_length(right, HEADER_SIZE), HEADER_BASELINE), right, font=regular, fontsize=HEADER_SIZE)
        tw.append((LEFT_X, FOOTER_BASELINE), footer_left, font=regular, fontsize=HEADER_SIZE)
        page_no = f"Page {i + 1} of {total}"
        tw.append((RIGHT_X - regular.text_length(page_no, HEADER_SIZE), FOOTER_BASELINE), page_no, font=regular, fontsize=HEADER_SIZE)
        page.write_text(writers=[tw], color=grey)
    doc.subset_fonts()
    tmp = pdf_path.with_suffix(".tmp.pdf")
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    tmp.replace(pdf_path)


def render_pdf(report: Report, out_path: str | Path, edition: Edition = "colour", html_path: str | Path | None = None) -> Path:
    from playwright.sync_api import sync_playwright

    page_html = render_html(report, edition)
    if html_path:
        Path(html_path).write_text(page_html, encoding="utf-8")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(page_html, wait_until="load")
            page.evaluate("document.fonts.ready")
            page.emulate_media(media="print")
            page.pdf(
                path=str(out_path),
                format="Letter",
                print_background=True,
                margin={k: f"{v / 72:.4f}in" for k, v in PAGE_MARGINS_PT.items()},
            )
        finally:
            browser.close()
    _stamp_running(out_path, report, edition)
    return out_path
