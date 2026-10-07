"""Print first/last body line per page for ours vs reference (page-flow check)."""
import sys
import pymupdf

sys.stdout.reconfigure(encoding="utf-8")
a, b = pymupdf.open(sys.argv[1]), pymupdf.open(sys.argv[2])


def firstlast(p):
    ls = []
    for bl in p.get_text("dict")["blocks"]:
        for l in bl.get("lines", []):
            t = "".join(s["text"] for s in l["spans"]).strip()
            if t and 30 < l["bbox"][1] < 760:
                ls.append((round(l["bbox"][1], 1), t[:42]))
    ls.sort()
    return ls[0], ls[-1]


print("pages ours", a.page_count, "ref", b.page_count)
for i in range(max(a.page_count, b.page_count)):
    fa = firstlast(a[i]) if i < a.page_count else None
    fb = firstlast(b[i]) if i < b.page_count else None
    same = fa and fb and fa[0][1][:20] == fb[0][1][:20] and fa[1][1][:20] == fb[1][1][:20]
    print(f"{i+1:2d} {'OK ' if same else '-- '} ours {fa}\n       ref  {fb}")
