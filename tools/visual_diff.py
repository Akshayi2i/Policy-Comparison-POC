"""Render pages of two PDFs side by side into PNGs for visual comparison.

usage: python tools/visual_diff.py ours.pdf reference.pdf out_dir [--pages 1,2,5] [--zoom 1.6]
Pages are 1-based. Missing pages on either side are left blank.
"""
import argparse
import pathlib

import pymupdf


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ours")
    ap.add_argument("reference")
    ap.add_argument("out_dir")
    ap.add_argument("--pages", default="")
    ap.add_argument("--zoom", type=float, default=1.6)
    ap.add_argument("--clip", default="", help="x0,y0,x1,y1 in PDF points to crop both pages")
    args = ap.parse_args()

    a, b = pymupdf.open(args.ours), pymupdf.open(args.reference)
    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    count = max(a.page_count, b.page_count)
    pages = [int(p) for p in args.pages.split(",") if p] or list(range(1, count + 1))
    clip = pymupdf.Rect(*map(float, args.clip.split(","))) if args.clip else None

    for n in pages:
        w, h = (clip.width, clip.height) if clip else (612, 792)
        sheet = pymupdf.open()
        page = sheet.new_page(width=w * 2 + 12, height=h)
        for i, doc in enumerate((a, b)):
            if n <= doc.page_count:
                page.show_pdf_page(pymupdf.Rect(i * (w + 12), 0, i * (w + 12) + w, h), doc, n - 1, clip=clip)
        page.draw_line((w + 6, 0), (w + 6, h), color=(1, 0, 0), width=1)
        page.get_pixmap(matrix=pymupdf.Matrix(args.zoom, args.zoom)).save(out / f"page_{n:02d}.png")
        print(out / f"page_{n:02d}.png")


if __name__ == "__main__":
    main()
