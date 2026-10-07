"""Deterministic comparison of flattened elements and policy-level facts (period, parts, market status)."""
from __future__ import annotations

import re
from typing import Optional

from policy_compare.findings import Finding
from policy_compare.flatten import Cell, Element, rule_matches
from policy_compare.fmt import duration, mdy, money, pct, signed_money
from policy_compare.ingest import Side, continuity
from policy_compare.settings import config
from policy_compare.textindex import TextIndex, norm_key


class Ids:
    def __init__(self) -> None:
        self.n = 0

    def next(self) -> str:
        self.n += 1
        return f"f{self.n:03d}"


def _rub() -> dict:
    return config("rubric")


def why(name: str, **kw) -> str:
    tpl = _rub().get("why_templates", {}).get(name, "")
    try:
        return tpl.format(**kw)
    except (KeyError, IndexError):
        return tpl


def _band(rules: list[dict], pct_abs: float) -> str:
    for r in rules:
        if pct_abs >= r["min_pct"]:
            return r["severity"]
    return rules[-1]["severity"]


def disp(cell: Optional[Cell]) -> str:
    if cell is None or not cell.present:
        return "—"
    return cell.text or "—"


def _ref(side: Side, page: Optional[int]) -> Optional[str]:
    return f"{side.ref} p.{page}" if page else None


def short_party(name: str) -> str:
    """'Keystone Casualty Company' -> 'Keystone Casualty' (used in 'A → B' change text)."""
    s = re.sub(r"\b(insurance|ins\.?|company|co\.?|corporation|corp\.?|inc\.?|llc|mutual)\b", "", name, flags=re.I)
    s = re.sub(r"[(),]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.title() if s.isupper() else (s or name)


def _clip(s: str, n: int = 40) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def compare_values(el_e: Optional[Element], el_r: Optional[Element], E: Side, R: Side, ti_e: TextIndex, ti_r: TextIndex,
                   ids: Ids) -> Optional[Finding]:
    el = el_e or el_r
    ce, cr = (el_e.cell if el_e else None), (el_r.cell if el_r else None)
    pe, pr = bool(ce and ce.present), bool(cr and cr.present)
    if not pe and not pr:
        return None
    f = Finding(id=ids.next(), change_key=f"value:{el.key}", section=el.section, kind="value", label=el.label, group=el.group,
                exp=disp(ce), ren=disp(cr), always_show=el.always_show, element_key=el.key, type=el.type)
    f.context["json_path"] = el.json_path
    if el_e and el_e.amended:
        f.tag = "AMENDED MID-TERM"
    # page references
    pg_e = (el_e.page_hint if el_e else None) or (ti_e.locate(ce.text, el.label) if pe else None)
    pg_r = (el_r.page_hint if el_r else None) or (ti_r.locate(cr.text, el.label) if pr else None)
    f.exp_ref, f.ren_ref = (_ref(E, pg_e) if pe else None), (_ref(R, pg_r) if pr else None)

    if pe and pr and ce.compare_key == cr.compare_key:
        f.change = "No change"
        return f

    rub = _rub()
    t, role = el.type, el.role
    lab = el.label

    def set_(impact, severity, change, why_text=None, locked=True):
        f.impact, f.severity, f.change, f.locked = impact, severity, change, locked
        f.why = why_text

    # one side only
    if pe and not pr:
        block = {"limit": "limits", "deductible": "deductibles", "premium": "premium"}.get(t, "terms")
        r = rub.get(block, {}).get("missing_in_renewal", {"impact": "confirm", "severity": "medium"})
        set_(r["impact"], r["severity"], "Not found in renewal", why("missing_in_renewal", label=lab, exp=f.exp), locked=True)
        f.ren = "—"
        return f
    if pr and not pe:
        block = {"limit": "limits", "deductible": "deductibles"}.get(t)
        r = rub.get(block, {}).get("new_in_renewal", {"impact": "confirm", "severity": "low"}) if block else {"impact": "confirm", "severity": "low"}
        set_(r["impact"], r["severity"], "New on renewal", why("new_in_renewal", label=lab, ren=f.ren), locked=False)
        return f

    # roles
    if role in ("effective_date", "expiration_date"):
        term = (E.expiration - E.effective).days if (E.expiration and E.effective) else 365
        a, b = ce.raw, cr.raw
        from policy_compare.ingest import parse_date
        da, db = parse_date(a), parse_date(b)
        tol = rub.get("term_tolerance_days", 3)
        rolled = bool(da and db and abs((db - da).days - term) <= tol)
        r = rub["policy"]["date_rollover"] if rolled else rub["policy"]["default_change"]
        set_(r["impact"], r["severity"], f"{f.exp} → {f.ren}", None, locked=rolled)
        return f
    if role == "policy_number":
        r = rub["policy"]["policy_number_changed"]
        set_(r["impact"], r["severity"], "New policy number")
        return f
    if role == "insurer":
        if norm_key(short_party(ce.text or "")) == norm_key(short_party(cr.text or "")):
            f.change = "No change"
            return f
        r = rub["policy"]["insurer_changed"]
        set_(r["impact"], r["severity"], f"{short_party(ce.text or '')} → {short_party(cr.text or '')}", why("insurer_changed"))
        f.context["insurer_change"] = True
        return f
    if role == "named_insured":
        if re.sub(r"[^a-z0-9]", "", (ce.text or "").lower()) == re.sub(r"[^a-z0-9]", "", (cr.text or "").lower()):
            f.change = "No change"
            return f
        r = rub["policy"]["named_insured_changed"]
        set_(r["impact"], r["severity"], "Named insured differs", why("named_insured_changed"))
        return f

    numeric = ce.kind == "amount" and cr.kind == "amount" and ce.amount is not None and cr.amount is not None
    if numeric:
        delta = cr.amount - ce.amount
        p_abs = abs(delta) / ce.amount * 100 if ce.amount else 100.0
        word = "Decreased" if delta < 0 else "Increased"
        change = f"{word} {money(abs(delta))}" + (f" ({pct(delta, ce.amount)})" if ce.amount else "")
        f.context.update({"expiring_amount": ce.amount, "renewal_amount": cr.amount, "delta": delta,
                          "pct": round(delta / ce.amount * 100, 1) if ce.amount else None})
        if t == "premium":
            r = rub["premium"]["changed"]
            set_(r["impact"], r["severity"], change, None)
        elif t == "deductible":
            if delta > 0:
                set_("reduced", _band(rub["deductibles"]["increase"], p_abs), change, why("deductible_increase"))
            else:
                r = rub["deductibles"]["decrease"]
                set_(r["impact"], r["severity"], change, why("deductible_decrease"))
        elif t == "limit":
            if delta < 0:
                set_("reduced", _band(rub["limits"]["decrease"], p_abs), change, why("limit_decrease"))
            else:
                set_("improved", _band(rub["limits"]["increase"], p_abs), change, why("limit_increase"))
        else:
            r = rub["terms"]["default_change"]
            set_(r["impact"], r["severity"], change, why("text_changed"), locked=False)
        return f

    # kind transitions (None / Included / Excluded / See form ...)
    change = f"{_clip(f.exp)} → {_clip(f.ren)}"
    if t == "limit" and cr.kind == "none" and ce.kind in ("amount", "included"):
        r = rub["limits"]["removed"]
        set_(r["impact"], r["severity"], change, why("limit_removed"))
    elif t == "limit" and ce.kind == "none" and cr.kind in ("amount", "included"):
        r = rub["limits"]["new_in_renewal"]
        set_(r["impact"], r["severity"], change, why("limit_increase"))
    elif t == "deductible" and ce.kind == "none" and cr.kind == "amount":
        r = rub["deductibles"]["introduced"]
        set_(r["impact"], r["severity"], change, why("deductible_introduced", ren=f.ren))
    elif t == "deductible" and cr.kind == "none" and ce.kind == "amount":
        r = rub["deductibles"]["decrease"]
        set_(r["impact"], r["severity"], change, why("deductible_decrease"))
    elif t == "premium":
        r = rub["premium"]["changed"]
        set_(r["impact"], r["severity"], change, None)
    else:
        r = rub["terms"]["default_change"]
        set_(r["impact"], r["severity"], change, why("text_changed"), locked=False)
    return f


def compare_elements(e: dict[str, Element], r: dict[str, Element], E: Side, R: Side, ti_e: TextIndex, ti_r: TextIndex,
                     ids: Ids) -> list[Finding]:
    out = []
    for key in list(dict.fromkeys([*e.keys(), *r.keys()])):
        el = e.get(key) or r.get(key)
        if el.section == "ignore" or el.role == "time_basis":
            continue
        f = compare_values(e.get(key), r.get(key), E, R, ti_e, ti_r, ids)
        if f:
            out.append(f)
    return _merge_duplicates(out)


def _merge_duplicates(findings: list[Finding]) -> list[Finding]:
    """The same value printed in two places (e.g. declarations and a form schedule) is one change:
    later copies point at the first via duplicate_of and are not counted again."""
    seen: dict[tuple, Finding] = {}
    for f in findings:
        if not f.changed:
            continue
        base = re.sub(r"\s+—\s+(limit|value|amount)$", "", f.label.lower())
        k = (f.section, norm_key(base), f.exp, f.ren)
        if k in seen:
            f.duplicate_of = seen[k].change_key
            f.change_key = seen[k].change_key
        else:
            seen[k] = f
    return findings


# ---------------- policy-level facts ----------------

def policy_level(E: Side, R: Side, ti_e: TextIndex, ti_r: TextIndex, e_el: dict[str, Element], r_el: dict[str, Element],
                 ids: Ids) -> list[Finding]:
    rub = _rub()["policy"]
    out: list[Finding] = []

    # continuity of cover
    gap = continuity(E, R)
    if gap is not None:
        pe = ti_e.locate(mdy(E.expiration), "policy period")
        pr = ti_r.locate(mdy(R.effective), "policy period")
        f = Finding(id=ids.next(), change_key="continuity", section="policy", kind="continuity", label="Policy period continuity",
                    exp=f"Expires {mdy(E.expiration)}", ren=f"Incepts {mdy(R.effective)}", exp_ref=_ref(E, pe), ren_ref=_ref(R, pr),
                    always_show=True, type="date")
        secs = gap.total_seconds()
        f.context.update({"gap_minutes": int(secs // 60), "expiring_time": str(E.time_basis or "not stated"),
                          "renewal_time": str(R.time_basis or "not stated")})
        if secs > 0:
            r = rub["continuity_gap"]
            f.impact, f.severity, f.locked = r["impact"], r["severity"], True
            f.change = f"Gap of {duration(gap)}"
            f.why = why("continuity_gap", gap=duration(gap))
            f.context["gap_text"] = duration(gap)
        elif secs < 0:
            r = rub["continuity_overlap"]
            f.impact, f.severity, f.locked = r["impact"], r["severity"], True
            f.change = f"Overlap of {duration(gap)}"
            f.why = why("continuity_overlap", gap=duration(gap))
        else:
            f.change = "Continuous (no gap)"
        out.insert(0, f)

    # coverage parts
    parts_cfg: dict = config("catalogue").get("coverage_parts", {})

    def parts(raw: dict, els: dict[str, Element]) -> list[str]:
        present = []
        for key, label in parts_cfg.items():
            if key in raw and any(el.container == key and el.cell.present and el.section != "ignore" for el in els.values()):
                present.append(label)
        return present

    pe_, pr_ = parts(E.data, e_el), parts(R.data, r_el)
    if pe_ or pr_:
        dropped = [p for p in pe_ if p not in pr_]
        added = [p for p in pr_ if p not in pe_]
        f = Finding(id=ids.next(), change_key="coverage_parts", section="policy", kind="coverage_parts",
                    label="Coverage parts (lines of business)", exp=", ".join(pe_) or "—", ren=", ".join(pr_) or "—",
                    exp_ref=_ref(E, ti_e.declarations_pages[0] if ti_e.declarations_pages else None),
                    ren_ref=_ref(R, ti_r.declarations_pages[0] if ti_r.declarations_pages else None), always_show=True)
        f.context.update({"dropped": dropped, "added": added})
        if dropped:
            r = rub["coverage_part_dropped"]
            f.impact, f.severity, f.locked = r["impact"], r["severity"], True
            f.change = "Dropped: " + ", ".join(dropped)
            f.why = why("coverage_part_dropped", label=", ".join(dropped))
        elif added:
            r = rub["coverage_part_added"]
            f.impact, f.severity, f.locked = r["impact"], r["severity"], True
            f.change = "Added: " + ", ".join(added)
            f.why = why("coverage_part_added", label=", ".join(added))
        out.append(f)

    # admitted / surplus lines status
    sig = config("catalogue").get("signals", {}).get("surplus_lines", [])
    he, hr = ti_e.search_any(sig), ti_r.search_any(sig)
    f = Finding(id=ids.next(), change_key="admitted_status", section="policy", kind="admitted",
                label="Admitted / surplus lines status",
                exp="Non-admitted (surplus lines)" if he else "No surplus lines wording found",
                ren="Non-admitted (surplus lines)" if hr else "No surplus lines wording found",
                exp_ref=_ref(E, he[0][0]) if he else None, ren_ref=_ref(R, hr[0][0]) if hr else None)
    if hr and not he:
        r = rub["now_non_admitted"]
        f.impact, f.severity, f.locked, f.change, f.why = r["impact"], r["severity"], True, "Now non-admitted", why("now_non_admitted")
    elif he and not hr:
        f.impact, f.severity, f.locked, f.change = "confirm", "medium", False, "No surplus lines wording on renewal"
        f.why = why("text_changed")
    out.append(f)
    return out


def expected_missing(section: str, e_el: dict[str, Element], r_el: dict[str, Element]) -> list[str]:
    """Labels of expected elements present in neither policy (footnote 'Not shown on either declarations page')."""
    from policy_compare.flatten import _match_segments
    out = []
    for item in config("catalogue").get("expected", {}).get(section, []):
        found = False
        for els in (e_el, r_el):
            for el in els.values():
                if el.cell.present and any(_match_segments(p.split("."), el.pattern.split(".")) for p in item["any"]):
                    found = True
        if not found:
            out.append(item["label"])
    return out


def expected_status(section: str, an) -> tuple[list[str], list[str]]:
    """(labels missing everywhere, 'label — form' notes for items not on the declarations but provided by a form)."""
    missing, via_form = [], []
    items = {i["label"]: i for i in config("catalogue").get("expected", {}).get(section, [])}
    for label in expected_missing(section, an.e_el, an.r_el):
        words = [w.lower() for w in items.get(label, {}).get("forms_any", [])]
        hits = {}
        for side, forms in (("expiring", an.fe), ("renewal", an.fr)):
            for f in forms.values():
                if any(w in f.title.lower() for w in words):
                    hits.setdefault(f.number, (f, set()))[1].add(side)
        if hits:
            parts = []
            for num, (f, sides) in hits.items():
                where = "both policies" if len(sides) == 2 else f"{next(iter(sides))} only"
                parts.append(f"{num} {f.title} ({where})")
            via_form.append(f"{label} — {'; '.join(parts)}")
        else:
            missing.append(label)
    return missing, via_form
