"""Compare text-line positions between our PDF and the reference, page by page.

usage: python tools/layout_diff.py ours.pdf reference.pdf --page 1 [--their-page 1]
Prints, for every reference line, the matching line in ours with dx / dy / dwidth (pt).
"""
import argparse
import sys

import pymupdf


def lines(page):
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            t = "".join(s["text"] for s in l["spans"]).strip()
            if t:
                out.append((t, l["bbox"], l["spans"][0]["size"]))
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("ours")
    ap.add_argument("reference")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--their-page", type=int)
    a = ap.parse_args()
    ours = lines(pymupdf.open(a.ours)[a.page - 1])
    ref = lines(pymupdf.open(a.reference)[(a.their_page or a.page) - 1])
    used = set()
    for t, bb, sz in ref:
        key = t[:22]
        match = next((i for i, (u, _, _) in enumerate(ours) if i not in used and u.startswith(key)), None)
        if match is None:
            print(f"   MISSING  y={bb[1]:6.1f} sz={sz:4.1f} | {t[:60]}")
            continue
        used.add(match)
        _, ob, osz = ours[match]
        print(f"dx={ob[0]-bb[0]:+6.1f} dy={ob[1]-bb[1]:+6.1f} dw={(ob[2]-ob[0])-(bb[2]-bb[0]):+6.1f} y={bb[1]:6.1f} sz={sz:4.1f}/{osz:4.1f} | {t[:55]}")


if __name__ == "__main__":
    main()
