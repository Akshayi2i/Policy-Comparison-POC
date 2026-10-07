"""Column-aware text extraction: returns reading-order lines per page."""
import bisect
import sys
import pymupdf

SPLIT_GAP = 25  # pt gap between spans that signals a column/table-cell break


def _join_spans(spans):
    text = spans[0]["text"]
    for prev, s in zip(spans, spans[1:]):
        gap = s["bbox"][0] - prev["bbox"][2]
        if gap > 1.0 and not text.endswith(" ") and not s["text"].startswith(" "):
            text += " "
        text += s["text"]
    return " ".join(text.split())


CHAR_GAP = 9  # pt gap between two visible characters that splits a segment
WORD_GAP_FACTOR = 0.07  # gap (relative to glyph box height) treated as a missing space


def is_hidden_span(span):
    """White (or near-white) text on these white pages is invisible when printed."""
    c = span["color"]
    return ((c >> 16) & 255) > 240 and ((c >> 8) & 255) > 240 and (c & 255) > 240


def hidden_spans(page):
    out = []
    d = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP)
    for b in d["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            for s in ln["spans"]:
                if s["text"].strip() and is_hidden_span(s):
                    out.append(s)
    return out


def page_lines(page):
    """Return list of (x0, y0, x1, y1, text) for every visible text segment on the page.
    Segments are split wherever two consecutive visible characters on a line are
    more than CHAR_GAP apart (column gutters, table cells)."""
    out = []
    d = page.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP)
    for b in d["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            chars = []
            for s in ln["spans"]:
                if is_hidden_span(s):
                    continue
                for ch in s["chars"]:
                    chars.append((ch["bbox"], ch["c"]))
            chars.sort(key=lambda c: c[0][0])
            segs, cur, last_vis = [], [], None
            for bbox, c in chars:
                if c.strip():
                    if last_vis is not None and bbox[0] - last_vis[2] > CHAR_GAP:
                        segs.append(cur)
                        cur = []
                    elif (last_vis is not None and cur and cur[-1][1].strip()
                          and bbox[0] - last_vis[2] > WORD_GAP_FACTOR * (bbox[3] - bbox[1])):
                        cur.append((None, " "))  # word gap with no space glyph
                    last_vis = bbox
                    cur.append((bbox, c))
                elif cur:
                    cur.append((bbox, c))
            if cur:
                segs.append(cur)
            for seg in segs:
                vis = [bb for bb, c in seg if c.strip()]
                if not vis:
                    continue
                text = "".join(c for _, c in seg)
                out.append((
                    min(bb[0] for bb in vis), min(bb[1] for bb in vis),
                    max(bb[2] for bb in vis), max(bb[3] for bb in vis),
                    " ".join(text.split()),
                ))
    return out


def group_rows(lines):
    """Group segments into visual rows (same baseline), each sorted left-to-right."""
    lines = sorted(lines, key=lambda l: ((l[1] + l[3]) / 2, l[0]))
    rows, cur, cur_y = [], [], None
    for l in lines:
        yc = (l[1] + l[3]) / 2
        if cur and abs(yc - cur_y) > 3:
            rows.append(cur)
            cur = []
        if not cur:
            cur_y = yc
        cur.append(l)
    if cur:
        rows.append(cur)
    for r in rows:
        r.sort(key=lambda l: l[0])
    return rows


def row_cells(page):
    """Visible text of the page as rows of cells: [[(x0, y0, x1, y1, text), ...], ...]."""
    return group_rows(page_lines(page))


def _rows(lines, sep=" "):
    """Group segments into visual rows (same baseline) and join left-to-right."""
    out = []
    for r in group_rows(lines):
        parts = [r[0][4]]
        for prev, l in zip(r, r[1:]):
            parts.append(sep if l[0] - prev[2] > SPLIT_GAP else " ")
            parts.append(l[4])
        out.append("".join(parts))
    return out


def find_gutter(page, lines=None):
    """Return the x of the column gutter for a two-column page, else None.
    The gutter is the x (in the middle 40-60% of the page) crossed by the fewest
    text segments; both sides must carry real prose, not just table values."""
    lines = lines if lines is not None else page_lines(page)
    w = page.rect.width
    body = [l for l in lines if len(l[4]) >= 12]  # short header bits/markers don't define the gutter
    best = None
    for x in range(int(0.4 * w), int(0.6 * w)):
        cross = sum(1 for l in body if l[0] < x - 1 and l[2] > x + 1)
        if best is None or cross < best[1]:
            best = (x, cross, [x])
        elif cross == best[1]:
            best[2].append(x)
    if best is None:
        return None
    # centre of the first contiguous run of best positions
    run = [best[2][0]]
    for x in best[2][1:]:
        if x == run[-1] + 1:
            run.append(x)
        else:
            break
    g = run[len(run) // 2]
    left = [l for l in lines if l[2] <= g + 1 and len(l[4]) >= 15]
    right = [l for l in lines if l[0] >= g - 1 and len(l[4]) >= 15]
    if len(left) >= 5 and len(right) >= 5 and best[1] <= 0.3 * len(body):
        return g
    return None


def is_two_column(page, lines=None):
    return find_gutter(page, lines) is not None


def reading_order(page, table_sep="  ", force_rows=False):
    """Text lines in reading order. Two-column pages read left column then right
    column within each band separated by full-width lines."""
    lines = page_lines(page)
    if not lines:
        return []
    g = None if force_rows else find_gutter(page, lines)
    if g is None:
        return _rows(lines, sep=table_sep)
    full = sorted([l for l in lines if l[0] < g - 1 and l[2] > g + 1], key=lambda l: l[1])
    rest = [l for l in lines if not (l[0] < g - 1 and l[2] > g + 1)]
    full_y = [f[1] for f in full]
    bands = [[] for _ in range(len(full) + 1)]
    for l in rest:
        bands[bisect.bisect_right(full_y, l[1] + 1)].append(l)
    result = []
    for i, band in enumerate(bands):
        lc = [l for l in band if l[2] <= g + 1]
        rc = [l for l in band if l[2] > g + 1]
        result.extend(_rows(lc))
        result.extend(_rows(rc))
        if i < len(full):
            result.append(full[i][4])
    return result


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    pages = [int(p) for p in sys.argv[3].split(",")] if len(sys.argv) > 3 else None
    doc = pymupdf.open(src)
    with open(out, "w", encoding="utf-8") as f:
        for i, page in enumerate(doc):
            if pages and (i + 1) not in pages:
                continue
            f.write(f"\n===== PAGE {i + 1} ({'2col' if is_two_column(page) else '1col'}) =====\n")
            f.write("\n".join(reading_order(page)))
            f.write("\n")
