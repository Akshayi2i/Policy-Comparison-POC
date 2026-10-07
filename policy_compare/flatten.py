"""Generic walk of a canonical policy JSON into comparable elements, aligned by identity (not position)."""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from policy_compare.ingest import parse_date
from policy_compare.settings import config
from policy_compare.textindex import norm_key

CELL_KINDS = {"amount", "none", "included", "see_form", "blank", "not_stated", "text"}
EMPTY_KINDS = {"blank", "not_stated", "empty"}
PRIMARY_VALUE_KEYS = {"limit", "value", "amount"}
GENERIC_LABELS = {"premium", "limit", "deductible", "limit each occurrence", "blanket limit", "package"}


@dataclass
class Cell:
    kind: str                    # amount | none | included | see_form | blank | not_stated | text | date | bool | number | empty
    amount: Optional[float] = None
    text: Optional[str] = None   # display text, as printed where available
    raw: Any = None

    @property
    def present(self) -> bool:
        return self.kind not in EMPTY_KINDS

    @property
    def compare_key(self) -> str:
        if self.kind in ("amount", "number") and self.amount is not None:
            return f"num:{self.amount:g}"
        if self.kind == "date":
            return f"date:{self.raw}"
        if self.kind in EMPTY_KINDS:
            return "empty"
        if self.kind in ("none", "included", "see_form"):
            return f"kind:{self.kind}"
        return "text:" + norm_key(self.text or "")


@dataclass
class Element:
    key: str                     # identity path, e.g. package_endorsement.coverages[coverage=computer coverage].limit
    pattern: str                 # path with list identities removed, e.g. package_endorsement.coverages.limit
    json_path: str               # $.package_endorsement.coverages[2].limit (audit trail)
    label: str
    cell: Cell
    container: str               # top-level key
    group: str = ""
    page_hint: Optional[int] = None
    section: str = "terms"
    type: str = "text"
    role: Optional[str] = None
    always_show: bool = False
    classified_by: str = "rule"  # rule | model | default
    item_label: Optional[str] = None
    amended: bool = False        # value replaced by a mid-term change (Section 8)


def humanize(key: str) -> str:
    s = re.sub(r"[_\-]+", " ", key).strip()
    return s[:1].upper() + s[1:] if s else key


def titlecase(s: str) -> str:
    small = {"and", "or", "of", "the", "a", "an", "to", "for", "in", "on"}
    words = re.split(r"(\s+)", s.lower())
    out = []
    for i, w in enumerate(words):
        out.append(w if (w in small and i) else w[:1].upper() + w[1:])
    return "".join(out).replace("&", "&")


def _fmt_date(d) -> str:
    return d.strftime("%m/%d/%Y")


def to_cell(node: Any) -> Cell:
    if isinstance(node, dict) and "kind" in node and ("amount" in node or "text" in node):
        kind = node.get("kind") if node.get("kind") in CELL_KINDS else "text"
        text = node.get("text")
        amount = node.get("amount")
        if kind == "none":
            text = "None"
        elif kind == "included":
            text = text or "Included"
        elif kind == "see_form":
            text = text or "See form"
        elif kind in EMPTY_KINDS:
            text = None
        return Cell(kind=kind, amount=float(amount) if isinstance(amount, (int, float)) else None, text=text, raw=node)
    if isinstance(node, dict) and "text" in node:
        return Cell(kind="text" if node.get("text") else "empty", text=node.get("text"), raw=node)
    if node is None or node == "" or node == []:
        return Cell(kind="empty", raw=node)
    if isinstance(node, bool):
        return Cell(kind="bool", text="Yes" if node else "No", raw=node)
    if isinstance(node, (int, float)):
        return Cell(kind="number", amount=float(node), text=f"{node:,}" if isinstance(node, int) else f"{node:g}", raw=node)
    if isinstance(node, list):
        vals = [str(v) for v in node if v not in (None, "")]
        return Cell(kind="text" if vals else "empty", text="; ".join(vals) or None, raw=node)
    s = str(node).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        d = parse_date(s)
        return Cell(kind="date", text=_fmt_date(d) if d else s, raw=s)
    return Cell(kind="text", text=s, raw=node)


def _is_cell(node: Any) -> bool:
    return isinstance(node, dict) and "kind" in node and ("amount" in node or "text" in node)


def _is_text_object(node: Any, cfg: dict) -> bool:
    if not isinstance(node, dict):
        return False
    spec = cfg.get("text_objects", {})
    return all(k in node for k in spec.get("required_keys", ["text"])) and any(k in node for k in spec.get("any_of_keys", []))


class Flattener:
    """Flatten both sides together so list identity keys are chosen consistently."""

    def __init__(self, raw_e: dict, raw_r: dict):
        self.cfg = config("catalogue")
        self.meta_keys = set(self.cfg.get("metadata_keys", []))
        self.excluded = set(self.cfg.get("exclude_top_level", []))
        self.id_candidates = self.cfg.get("identity_keys", [])
        self.lists: dict[str, list[list[dict]]] = {}
        for raw in (raw_e, raw_r):
            self._collect_lists(raw, "")
        self.identity = {p: self._choose_key(groups) for p, groups in self.lists.items()}
        self.unaligned: list[str] = [p for p, k in self.identity.items() if k is None]

    def _collect_lists(self, node: Any, pattern: str) -> None:
        if isinstance(node, dict) and not _is_cell(node):
            for k, v in node.items():
                if not pattern and k in self.excluded:
                    continue
                self._collect_lists(v, f"{pattern}.{k}" if pattern else k)
        elif isinstance(node, list) and node and all(isinstance(x, dict) for x in node):
            self.lists.setdefault(pattern, []).append(node)
            for item in node:
                self._collect_lists(item, pattern)

    def _choose_key(self, groups: list[list[dict]]) -> Optional[str]:
        for cand in self.id_candidates:
            ok = True
            for items in groups:
                vals = [norm_key(str(it.get(cand))) for it in items if it.get(cand) not in (None, "")]
                if len(vals) != len(items) or len(set(vals)) != len(vals):
                    ok = False
                    break
            if ok:
                return cand
        return None

    def flatten(self, raw: dict) -> dict[str, Element]:
        out: dict[str, Element] = {}
        for k, v in raw.items():
            if k in self.excluded or k in self.meta_keys:
                continue
            self._walk(v, k, k, f"$.{k}", container=k, group=self._group_title(v, k), page=None, item_label=None, out=out)
        return out

    def _group_title(self, node: Any, key: str, parent: str = "") -> str:
        if isinstance(node, dict):
            title = node.get("section_title")
            if title and str(title).strip().upper() not in ("LOCATION SCHEDULE",):
                base = titlecase(str(title))
                if node.get("package_name"):
                    base += f" — {titlecase(str(node['package_name']))}"
                return base
            if node.get("form_number"):
                return f"{humanize(key)} ({node['form_number']})"
        return humanize(key) if not parent else parent

    def _walk(self, node, key, pattern, json_path, container, group, page, item_label, out, leaf_name=None):
        if isinstance(node, dict) and not _is_cell(node) and not _is_text_object(node, self.cfg):
            if isinstance(node.get("page"), int):
                page = node["page"]
            for k, v in node.items():
                if k in self.meta_keys:
                    continue
                child_group = self._group_title(v, k, group) if isinstance(v, dict) and pattern.count(".") == 0 else group
                self._walk(v, f"{key}.{k}", f"{pattern}.{k}", f"{json_path}.{k}", container, child_group, page, item_label, out, k)
            return
        if isinstance(node, list) and node and all(isinstance(x, dict) for x in node):
            id_key = self.identity.get(pattern)
            for i, item in enumerate(node):
                ident = item.get(id_key) if id_key else None
                ikey = f"{key}[{id_key}={norm_key(str(ident))}]" if id_key else f"{key}[{i}]"
                label = str(ident).strip() if ident not in (None, "") else None
                comparable = {k: v for k, v in item.items() if k not in self.meta_keys and k != id_key}
                numeric_id = label is not None and re.fullmatch(r"\d+", label) is not None
                for k, v in comparable.items():
                    if not label:
                        sub_label = None
                    elif numeric_id:   # e.g. building_number=1 -> "Construction (building 1)"
                        sub_label = f"{humanize(k)} ({humanize(id_key).replace(' number', '').lower()} {label})"
                    elif k in PRIMARY_VALUE_KEYS:
                        sub_label = label
                    else:
                        sub_label = f"{label} — {humanize(k).lower()}"
                    self._walk(v, f"{ikey}.{k}", f"{pattern}.{k}", f"{json_path}[{i}].{k}", container, group,
                               item.get("page") if isinstance(item.get("page"), int) else page, sub_label, out, k)
            return
        cell = to_cell(node)
        cell_label = node.get("label") if isinstance(node, dict) else None
        label = cell_label or item_label or humanize(leaf_name or key.split(".")[-1])
        if label.isupper():
            label = titlecase(label)
        if label.lower() in GENERIC_LABELS and group:
            label = f"{group} — {label.lower()}"
        out[key] = Element(key=key, pattern=pattern, json_path=json_path, label=label, cell=cell, container=container,
                           group=group, page_hint=page, item_label=item_label)


# ---------- classification ----------

def _match_segments(pat: list[str], path: list[str]) -> bool:
    if not pat:
        return not path
    if pat[0] == "**":
        return any(_match_segments(pat[1:], path[i:]) for i in range(len(path) + 1))
    return bool(path) and fnmatch.fnmatchcase(path[0], pat[0]) and _match_segments(pat[1:], path[1:])


def rule_matches(rule: dict, el: Element) -> bool:
    if "match" in rule and not _match_segments(rule["match"].split("."), el.pattern.split(".")):
        return False
    if "label" in rule and "match" not in rule:
        return fnmatch.fnmatchcase(el.label.lower(), rule["label"].lower())
    return "match" in rule


def classify(el: Element, rules: list[dict]) -> bool:
    """Apply the first matching catalogue rule. Returns False when no rule matched."""
    for rule in rules:
        if rule_matches(rule, el):
            el.section = rule.get("section", el.section)
            el.type = rule.get("type", el.type)
            el.role = rule.get("role")
            el.always_show = bool(rule.get("always_show", False))
            if rule.get("group"):
                el.group = rule["group"]
            if rule.get("label") and "match" in rule:
                el.label = rule["label"]
            el.classified_by = "rule"
            return True
    return False


def flatten_pair(raw_e: dict, raw_r: dict) -> tuple[dict[str, Element], dict[str, Element], list[Element], list[str]]:
    """Flatten and classify both sides. Returns (expiring, renewal, unmatched elements, unaligned list paths)."""
    fl = Flattener(raw_e, raw_r)
    e, r = fl.flatten(raw_e), fl.flatten(raw_r)
    rules = config("catalogue").get("rules", [])
    unmatched: dict[str, Element] = {}
    for side in (e, r):
        for el in side.values():
            if not classify(el, rules):
                el.classified_by = "default"
                unmatched.setdefault(el.pattern, el)
    return e, r, list(unmatched.values()), fl.unaligned
