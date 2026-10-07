"""Check two canonical JSON files share the same structure, and list value differences."""
import json
import sys


def paths(node, prefix=""):
    """Every field path (arrays collapsed to []) together with the JSON type found there."""
    out = set()
    if isinstance(node, dict):
        out.add((prefix or "$", "object"))
        for k, v in node.items():
            out |= paths(v, f"{prefix}.{k}")
    elif isinstance(node, list):
        out.add((prefix, "array"))
        for v in node:
            out |= paths(v, prefix + "[]")
    else:
        t = "null" if node is None else type(node).__name__
        out.add((prefix, "number" if t in ("int", "float") else t))
    return out


def flat(node, prefix=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from flat(v, f"{prefix}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from flat(v, f"{prefix}[{i}]")
    else:
        yield prefix, node


a = json.load(open(sys.argv[1], encoding="utf-8"))
b = json.load(open(sys.argv[2], encoding="utf-8"))

field_a = {p for p, _ in paths(a)}
field_b = {p for p, _ in paths(b)}
print(f"field paths: A={len(field_a)} B={len(field_b)} only_A={sorted(field_a - field_b)} only_B={sorted(field_b - field_a)}")

# a path may legitimately hold null in one file and a value in the other; flag anything else
types_a, types_b = {}, {}
for p, t in paths(a):
    types_a.setdefault(p, set()).add(t)
for p, t in paths(b):
    types_b.setdefault(p, set()).add(t)
for p in sorted(field_a & field_b):
    ta, tb = types_a[p] - {"null"}, types_b[p] - {"null"}
    if ta and tb and ta != tb:
        print(f"type mismatch at {p}: {ta} vs {tb}")

print("\nvalue differences outside documents[] and source_document:")
fa = dict(flat({k: v for k, v in a.items() if k not in ("documents", "source_document")}))
fb = dict(flat({k: v for k, v in b.items() if k not in ("documents", "source_document")}))
for k in sorted(set(fa) | set(fb)):
    if fa.get(k, "<absent>") != fb.get(k, "<absent>"):
        print(f"  {k}: {fa.get(k, '<absent>')!r} -> {fb.get(k, '<absent>')!r}")
