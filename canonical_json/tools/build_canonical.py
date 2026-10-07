"""Build a canonical JSON representation of a Utica First Contractors Special Policy PDF.

Usage: python build_canonical.py <policy.pdf> <out.json>

Every page's visible text is kept (column-aware reading order) under documents[].pages[],
segmented by the PDF's own bookmarks. Declarations and fill-in schedule data are parsed
into structured fields. Hidden text, images and checkbox marks are recorded per page.
"""
import collections
import datetime
import hashlib
import json
import os
import re
import sys

import pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from colextract import find_gutter, hidden_spans, reading_order, row_cells  # noqa: E402

SCHEMA = {"name": "canonical_insurance_policy", "version": "1.0.0"}

# Images verified visually; keyed by (document form_number, image pixel size of the strips).
IMAGE_DESCRIPTIONS = {
    ("APDEC", (80, 93)): ("Utica First Insurance Company logo mark", None),
    ("APDEC", (371, 119)): ("Handwritten signature of the authorized representative (countersignature)", None),
    ("PRIV", (84, 1)): ("Utica First Insurance Company logo mark (rendered as image strips)", None),
    ("SNEXNY", (416, 1)): ("Utica First Insurance Company logo with wordmark (rendered as image strips)",
                           "UTICA FIRST INSURANCE COMPANY"),
    ("SROCNY", (416, 1)): ("Utica First Insurance Company logo with wordmark (rendered as image strips)",
                           "UTICA FIRST INSURANCE COMPANY"),
    ("UFR 1", (32, 2)): ("Utica First Insurance Company logo mark (rendered as image strips)", None),
    ("CP 382", (64, 1)): ("Copyright symbol preceding 'Copyright 1987 AAIS' (rendered as image strips)", "©"),
}


# ----------------------------------------------------------------------------- value helpers

def clean(s):
    return " ".join(s.split()) if s is not None else None


def money(text, absent="blank"):
    """Normalise a printed monetary/limit cell. 'text' keeps the printed value verbatim.
    `absent` is the kind used when nothing is printed: 'blank' for an empty table cell or
    fill-in line, 'not_stated' when the document simply does not state the value."""
    t = clean(text)
    if t is None or t in ("", "$"):
        return {"text": t or None, "amount": None, "kind": absent}
    if re.fullmatch(r"\$?\s*none", t, re.I):
        return {"text": t, "amount": None, "kind": "none"}
    if re.fullmatch(r"included", t, re.I):
        return {"text": t, "amount": None, "kind": "included"}
    if re.fullmatch(r"see form", t, re.I):
        return {"text": t, "amount": None, "kind": "see_form"}
    m = re.fullmatch(r"\$\s*(\d[\d,]*)(\.\d+)?", t)
    if m:
        whole = m.group(1).replace(",", "")
        amt = float(whole + m.group(2)) if m.group(2) else int(whole)
        return {"text": t, "amount": amt, "kind": "amount"}
    return {"text": t, "amount": None, "kind": "text"}


def iso_date(mdy):
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", mdy.strip())
    return f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else None


CITY_LINE = re.compile(r"^(?P<city>[^,]+),\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$")


def address(lines):
    lines = [clean(l) for l in lines if clean(l)]
    if len(lines) == 1 and lines[0].count(",") >= 2:
        street, rest = lines[0].split(",", 1)
        lines = [street.strip(), rest.strip()]
    m = CITY_LINE.match(lines[-1]) if lines else None
    return {
        "street_lines": lines[:-1] if m else lines,
        "city": m.group("city") if m else None,
        "state": m.group("state") if m else None,
        "postal_code": m.group("zip") if m else None,
        "text": ", ".join(lines),
    }


def pdf_date(s):
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", s or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T{m.group(4)}:{m.group(5)}:{m.group(6)}Z" if m else (s or None)


def edition_key(e):
    return re.sub(r"\D", "", e or "")


def code_key(c):
    return re.sub(r"[\s\-]", "", (c or "").upper())


# ----------------------------------------------------------------------------- row helpers

def row_texts(page):
    """Rows as tab-joined cells; a lone '$' cell is glued to the amount that follows it."""
    out = []
    for r in row_cells(page):
        cells = [c[4] for c in r]
        txt = "\t".join(cells)
        out.append(re.sub(r"\$\t(?=\d)", "$ ", txt))
    return out


def find(rows, pattern, start=0):
    rx = re.compile(pattern)
    for i in range(start, len(rows)):
        m = rx.search(rows[i])
        if m:
            return i, m
    return None, None


def column_of(seg, headers):
    """Pick the header column whose left edge is nearest the cell's left edge."""
    return min(headers, key=lambda h: abs(h[0] - seg[0]))[1]


# ----------------------------------------------------------------------------- declarations

def parse_carrier(rows):
    carrier = {"name": rows[0]}
    _, m = find(rows, r"^CONSTITUTED IN (.+?) AS$")
    carrier["constitution_statement"] = m.group(0) if m else None
    carrier["constituted_in"] = m.group(1) if m else None
    i, _ = find(rows, r"^CONSTITUTED IN")
    carrier["constituted_as"] = rows[i + 1] if i is not None else None
    _, m = find(rows, r"^Home Office:\t(.+)$")
    carrier["home_office_address"] = address([m.group(1)]) if m else None
    _, m = find(rows, r"^Mailing Address:\t(.+)$")
    carrier["mailing_address"] = address([m.group(1)]) if m else None
    _, m = find(rows, r"^Phone:\t(.+)$")
    carrier["phone"] = m.group(1) if m else None
    return carrier


def parse_dec_main(page):
    rows = row_texts(page)
    text = "\n".join(rows)
    out = {}
    out["carrier"] = parse_carrier(rows)
    i, _ = find(rows, r"DECLARATIONS$")
    out["declarations_title"] = rows[i] if i is not None else None
    _, m = find(rows, r"^(CONTRACTORS SPECIAL POLICY) DECLARATIONS$")
    out["policy_type"] = m.group(1) if m else None
    _, m = find(rows, r"Policy Number:\s*(\S+)")
    out["policy_number"] = m.group(1) if m else None
    _, m = find(rows, r"Transaction Type:\t(.+)$")
    out["transaction_type"] = m.group(1) if m else None
    _, m = find(rows, r"Billing Type:\t(.+)$")
    out["billing_type"] = m.group(1) if m else None

    i_ni, _ = find(rows, r"^Named Insured and Mailing Address:$")
    i_ag, _ = find(rows, r"^Agent:$")
    i_pp, m_pp = find(rows, r"^Policy Period:\s*(.+)$")
    ni = rows[i_ni + 1:i_ag]
    ag = rows[i_ag + 1:i_pp]
    out["named_insured"] = {"name": ni[0], "mailing_address": address(ni[1:])}
    out["agent"] = {"name": ag[0], "address": address(ag[1:])}

    _, m = find(rows, r"^From (\d{2}/\d{2}/\d{4}) to (\d{2}/\d{2}/\d{4})$")
    out["policy_period"] = {
        "effective_date": iso_date(m.group(1)),
        "expiration_date": iso_date(m.group(2)),
        "effective_time_basis": m_pp.group(1),
        "text": m.group(0),
    }
    m = re.search(r"(IN RETURN FOR .*?POLICY\.)", text, re.S)
    out["insuring_agreement"] = clean(m.group(1)) if m else None
    m = re.search(r"ANNUAL PREMIUM:\s*(\$[\d,.]+)", text)
    out["annual_premium"] = dict(label="ANNUAL PREMIUM", **money(m.group(1))) if m else None
    m = re.search(r"(\$[\d,.]+)\s+(MINIMUM RETAINED PREMIUM)", text)
    out["minimum_retained_premium"] = dict(label=m.group(2), **money(m.group(1))) if m else None
    m = re.search(r"(Our Authorized Representative and)\s*\n\s*(Countersignature Date)\s+(\d{2}/\d{2}/\d{4})", text)
    out["countersignature"] = {
        "label": f"{m.group(1)} {m.group(2)}" if m else None,
        "date": iso_date(m.group(3)) if m else None,
        "signature_image_present": signature_above(page, "Our Authorized Representative"),
    }
    m = re.search(r"^(APDEC)\s+(\d+)\tPage \d+ of (\d+)$", text, re.M)
    out["declarations_form"] = {"form_number": m.group(1), "edition": m.group(2),
                                "page_count": int(m.group(3))} if m else None
    return out


GL_KEYS = {
    "Each Occurrence Limit": "each_occurrence",
    "General Aggregate Limit – other than Products/Completed Work": "general_aggregate_other_than_products_completed_work",
    "Aggregate Limit – Products/Completed Work": "products_completed_work_aggregate",
    "Medical Payments Limit": "medical_payments",
    "Personal & Advertising Injury Limit": "personal_and_advertising_injury",
    "Fire Legal Liability Limit": "fire_legal_liability",
}


def dec_header(rows):
    _, m1 = find(rows, r"Policy Number:\s*(\S+)")
    _, m2 = find(rows, r"Policy Period:\s*(\d{2}/\d{2}/\d{4})\s*–\s*(\d{2}/\d{2}/\d{4})")
    return {
        "policy_number": m1.group(1) if m1 else None,
        "effective_date": iso_date(m2.group(1)) if m2 else None,
        "expiration_date": iso_date(m2.group(2)) if m2 else None,
    }


def parse_dec_gl_property(page):
    cells = row_cells(page)
    rows = row_texts(page)
    out = {"header": dec_header(rows)}
    _, m = find(rows, r"^Class Description:\s*(.+)$")
    out["class_description"] = m.group(1) if m else None

    i_gl, _ = find(rows, r"^GENERAL LIABILITY COVERAGE$")
    i_lp, m_lp = find(rows, r"^Liability Premium:\s*(\$[\d,.]+)$")
    _, m_hdr = find(rows, r"^Limit of Insurance$")
    limits, extra, ded = {}, [], None
    for r in rows[i_gl + 1:i_lp]:
        parts = r.split("\t")
        if len(parts) != 2:
            continue
        label, val = parts
        item = dict(label=label, **money(val))
        if label == "Property Damage Deductible":
            ded = item
        elif label in GL_KEYS:
            limits[GL_KEYS[label]] = item
        else:
            extra.append(item)
    out["general_liability"] = {
        "section_title": "GENERAL LIABILITY COVERAGE",
        "limit_column_heading": m_hdr.group(0) if m_hdr else None,
        "limits": {k: limits.get(k) for k in GL_KEYS.values()},
        "other_limits": extra,
        "property_damage_deductible": ded,
        "premium": dict(label="Liability Premium", **money(m_lp.group(1))) if m_lp else None,
    }

    # Location schedule: Location -> Building -> attributes and coverage lines
    i_ls, _ = find(rows, r"^Location Schedule$")
    locations, headers = [], None
    for r, rc in zip(rows[i_ls + 1:], cells[i_ls + 1:]):
        if re.match(r"^APDEC\s", r):
            break
        m = re.match(r"^Location:\s*(.+)$", r)
        if m:
            locations.append({"location_number": len(locations) + 1, "address": address([m.group(1)]),
                              "buildings": []})
            continue
        m = re.match(r"^Building #(\d+)$", r)
        if m:
            locations[-1]["buildings"].append({"building_number": int(m.group(1)), "construction": None,
                                               "county": None, "valuation": None, "cause_of_loss": None,
                                               "coverages": []})
            continue
        m = re.match(r"^(Construction|County|Valuation|Cause of Loss):\t(.+)$", r)
        if m:
            key = m.group(1).lower().replace(" ", "_")
            locations[-1]["buildings"][-1][key] = m.group(2)
            continue
        if all(c[4] in ("Limit", "Premium") for c in rc):
            headers = [(c[0], c[4].lower()) for c in rc]
            continue
        line = {"description": rc[0][4], "limit": money(None), "premium": money(None)}
        for c in rc[1:]:
            line[column_of(c, headers)] = money(c[4])
        locations[-1]["buildings"][-1]["coverages"].append(line)
    out["property"] = {"section_title": "Location Schedule", "locations": locations}
    return out


def parse_dec_coverage_lists(page):
    cells = row_cells(page)
    rows = row_texts(page)
    out = {"header": dec_header(rows)}
    pkg = {"section_title": None, "package_name": None, "premium": None, "coverages": []}
    te = {"section_title": None, "note": None, "coverages": []}
    opt = {"section_title": None, "note": None, "coverages": []}
    section, headers = None, None
    for r, rc in zip(rows, cells):
        first = rc[0][4]
        if re.match(r"^APDEC\s", r):
            break
        if first == "PACKAGE ENDORSEMENT":
            section, headers = "pkg_head", [(c[0], c[4].lower()) for c in rc[1:]]
            pkg["section_title"] = first
            continue
        if first == "TOOLS & EQUIPMENT COVERAGE LIST":
            section, headers = "te", [(c[0], c[4].lower()) for c in rc[1:]]
            te["section_title"] = first
            continue
        if first == "OPTIONAL LIABILITY COVERAGE LIST":
            section, headers = "opt", None
            opt["section_title"] = first
            continue
        if section is None:
            continue
        if first.startswith("(See Forms"):
            (te if section == "te" else opt)["note"] = first
            continue
        if first == "Coverage":  # column heading row
            headers = [(c[0], c[4].lower()) for c in rc[1:]]
            if section == "pkg_head":
                section = "pkg"
            continue
        if section == "pkg_head":  # package name row, e.g. TOOLBOX $200
            pkg["package_name"] = first
            pkg["premium"] = money(rc[1][4]) if len(rc) > 1 else money(None)
            continue
        item = {"coverage": first}
        for _, h in headers:
            item[h] = money(None)
        for c in rc[1:]:
            item[column_of(c, headers)] = money(c[4])
        {"pkg": pkg, "te": te, "opt": opt}[section]["coverages"].append(item)
    out["package_endorsement"] = pkg
    out["tools_and_equipment_coverage_list"] = te
    out["optional_liability_coverage_list"] = opt
    return out


def parse_forms_inventory(page):
    cells = row_cells(page)
    rows = row_texts(page)
    out = {"header": dec_header(rows)}
    i_fi, m_fi = find(rows, r"^FORMS INVENTORY$")
    i_grp, m_grp = find(rows, r"^POLICY FORMS$", i_fi or 0)
    forms, title_x = [], None
    for r, rc in zip(rows[i_grp + 1:], cells[i_grp + 1:]):
        if re.match(r"^APDEC\s", r):
            break
        if len(rc) >= 3:
            title_x = rc[2][0]
            forms.append({"sequence": len(forms) + 1, "form_number": rc[0][4], "edition": rc[1][4],
                          "title": " ".join(c[4] for c in rc[2:])})
        elif len(rc) == 1 and title_x is not None and abs(rc[0][0] - title_x) < 5:
            forms[-1]["title"] += " " + rc[0][4]  # wrapped title line
    out["section_title"] = m_fi.group(0) if m_fi else None
    out["group"] = m_grp.group(0) if m_grp else None
    out["forms"] = forms
    return out


# ----------------------------------------------------------------------------- form schedules

def checkboxes(page):
    """Square checkbox outlines; checked when crossing lines are drawn inside the square."""
    drawings = page.get_drawings()
    boxes = []
    for d in drawings:
        r = d["rect"]
        if [it[0] for it in d["items"]] == ["re"] and 5 <= r.width <= 14 and abs(r.width - r.height) < 1.5:
            boxes.append(r)
    segs = [l for row in row_cells(page) for l in row]
    out = []
    boxes.sort(key=lambda r: r.y0)
    for k, b in enumerate(boxes):
        marks = [d for d in drawings
                 if [it[0] for it in d["items"]] == ["l"] and b.contains(d["rect"]) and d["rect"].width > 2]
        y_end = boxes[k + 1].y0 - 6 if k + 1 < len(boxes) else b.y1 + 14
        label = [s for s in segs
                 if b.x1 < s[0] < b.x1 + 40 and b.y0 - 6 <= (s[1] + s[3]) / 2 < y_end]
        out.append({
            "bbox": [round(v, 1) for v in (b.x0, b.y0, b.x1, b.y1)],
            "checked": len(marks) >= 2,
            "mark": "X" if len(marks) >= 2 else None,
            "label": " ".join(s[4] for s in sorted(label, key=lambda s: s[1])) or None,
        })
    return out


def parse_ap0660(page, ref):
    rows = row_texts(page)
    _, m = find(rows, r"Blanket Limit\t\$\s*([\d,]*)")
    _, m_rc = find(rows, r"^Replacement Cost\t(\$\s*[\d,]*)$")
    i_se, _ = find(rows, r"Scheduled Equipment")
    items = []
    if i_se is not None:
        for r in rows[i_se + 1:]:
            if r.startswith("Copyright"):
                break
            if r in ("Cost (Applies", "only when an", "entry is made for", "described item)"):
                continue  # wrapped column heading
            items.append(r)
    return dict(ref, **{
        "blanket_limit": dict(label="Blanket Limit", **money("$" + m.group(1) if m else None)),
        "blanket_replacement_cost": dict(label="Replacement Cost (Applies only if entry is made above)",
                                         **money(m_rc.group(1) if m_rc else None)),
        "scheduled_equipment": [{"description": r} for r in items],
    })


def parse_ap0661(page, ref):
    rows = row_texts(page)
    _, m = find(rows, r"^Limit\t\$\s*([\d,]+)$")
    i_d, m_rc = find(rows, r"^Description of Tools\tReplacement Cost applies\t?(.*)$")
    desc = []
    if i_d is not None:
        for r in rows[i_d + 1:]:
            if "\t" in r or r.isupper():
                break
            desc.append(r)
    return dict(ref, **{
        "limit": dict(label="Limit", **money("$" + m.group(1) if m else None)),
        "description_of_tools": " ".join(desc) or None,
        "replacement_cost_applies": (m_rc.group(1) or None) if m_rc else None,
    })


def parse_gl212(page, ref):
    boxes = checkboxes(page)
    box_x = min((b["bbox"][0] for b in boxes), default=page.rect.width)
    get = {}
    for row in row_cells(page):
        label = row[0][4]
        if label in ("Policy Number:", "Location:", "Description:"):
            vals = [c[4] for c in row[1:] if c[2] < box_x]  # cells left of the checkbox column
            get[label[:-1]] = " ".join(vals) or None
    return dict(ref, **{
        "policy_number": get.get("Policy Number"),
        "location": get.get("Location"),
        "description": get.get("Description"),
        "excluded_hazards": [{"hazard": b["label"], "checked": b["checked"]} for b in boxes],
    })


def parse_tlbx(page, ref):
    cells = row_cells(page)
    rows = row_texts(page)
    i_h, _ = find(rows, r"^COVERAGE\tLIMIT$")
    items = []
    for r, rc in zip(rows[i_h + 1:], cells[i_h + 1:]):
        if r.startswith("TLBX"):
            break
        if len(rc) >= 2:
            text = rc[0][4]
            m = re.match(r"^(.*?)\s+-\s+subject to (\$[\d,]+) deductible$", text)
            items.append({
                "coverage": m.group(1) if m else text,
                "coverage_text": text,
                "limit": money(rc[-1][4]),
                "deductible": money(m.group(2) if m else None, absent="not_stated"),
                "conditions": [],
            })
        elif items:
            items[-1]["conditions"].append(r)
    return dict(ref, **{"coverages": items})


def parse_gl242(page, ref):
    text = clean(" ".join(reading_order(page)))
    m = re.search(r"(Our limit for coverage under this endorsement is (\$[\d,]+) for each occurrence\.)", text)
    return dict(ref, **{
        "limit_each_occurrence": money(m.group(2) if m else None),
        "limit_text": m.group(1) if m else None,
    })


SCHEDULE_PARSERS = {
    "contractors_equipment_schedule": ("AP 0660", parse_ap0660),
    "contractors_tools_schedule": ("AP 0661", parse_ap0661),
    "toolbox_schedule": ("TLBX", parse_tlbx),
    "explosion_collapse_underground_property_damage_schedule": ("GL 212", parse_gl212),
    "incidental_liability_care_custody_control": ("GL 242", parse_gl242),
}


# ----------------------------------------------------------------------------- documents/pages

BM_RE = re.compile(r"^(?P<seq>\d+)\.\s+(?P<rest>.+)$")


def parse_bookmark(title, known_codes):
    m = BM_RE.match(title)
    seq, rest = int(m.group("seq")), m.group("rest")
    m = re.match(r"^(?P<code>.+?) \((?P<ed>\d\d-\d\d)\) (?P<title>.+)$", rest)
    if m:
        return seq, m.group("code"), m.group("ed"), m.group("title")
    m = re.match(r"^(?P<body>.+) (?P<ed>\d+\.\d+)$", rest)
    body, ed = (m.group("body"), m.group("ed")) if m else (rest, None)
    for code in sorted(known_codes, key=len, reverse=True):
        if body.startswith(code + " "):
            return seq, code, ed, body[len(code) + 1:]
    code, _, t = body.partition(" ")
    return seq, code, ed, t


def image_clusters(page):
    """Group image objects that touch each other (logos are often sliced into 1-px strips)."""
    clusters = []  # [rect, [images]]
    for im in page.get_images(full=True):
        bb = page.get_image_bbox(im)
        grown = pymupdf.Rect(bb.x0 - 8, bb.y0 - 8, bb.x1 + 8, bb.y1 + 8)
        hits = [c for c in clusters if c[0].intersects(grown)]
        merged = [pymupdf.Rect(bb), [im]]
        for c in hits:
            merged[0] |= c[0]
            merged[1] = c[1] + merged[1]
            clusters.remove(c)
        clusters.append(merged)
    clusters.sort(key=lambda c: (c[0].y0, c[0].x0))
    return clusters


def page_images(page, form_number):
    out = []
    for rect, ims in image_clusters(page):
        sizes = {(im[2], im[3]) for im in ims}
        desc, text_in_image = next((IMAGE_DESCRIPTIONS[(form_number, s)] for s in sizes
                                    if (form_number, s) in IMAGE_DESCRIPTIONS), (None, None))
        out.append({
            "bbox": [round(v, 1) for v in rect],
            "image_object_count": len(ims),
            "pixel_sizes": sorted(f"{w}x{h}" for w, h in sizes),
            "description": desc,
            "text_in_image": text_in_image,
        })
    return out


def signature_above(page, label):
    """True when an image sits on the signature line directly above the given label."""
    hits = page.search_for(label)
    if not hits:
        return False
    lab = hits[0]
    return any(r.y1 <= lab.y0 + 5 and r.y1 >= lab.y0 - 80 and r.x1 > lab.x0 and r.x0 < lab.x1
               for r, _ in image_clusters(page))


def build_page(page, form_number):
    g = find_gutter(page)
    return {
        "page_number": page.number + 1,
        "layout": "two_column" if g is not None else "single_column",
        "text": "\n".join(reading_order(page)),
        "hidden_text": [{
            "text": s["text"], "bbox": [round(v, 1) for v in s["bbox"]], "font": s["font"],
            "size": s["size"], "color": f"#{s['color']:06X}",
            "note": "Present in the PDF text layer but rendered in white, so it is not visible on the printed page.",
        } for s in hidden_spans(page)],
        "images": page_images(page, form_number),
        "checkboxes": checkboxes(page),
    }


def text_completeness(doc):
    bad = []
    for page in doc:
        ref = collections.Counter(re.sub(r"\s+", "", page.get_text("text")))
        got = collections.Counter(re.sub(r"\s+", "", "".join(reading_order(page))
                                         + "".join(s["text"] for s in hidden_spans(page))))
        if ref != got:
            bad.append(page.number + 1)
    return bad


# ----------------------------------------------------------------------------- main

def build(pdf_path):
    doc = pymupdf.open(pdf_path)
    raw = open(pdf_path, "rb").read()
    meta = doc.metadata
    toc = doc.get_toc()

    dec_pages = {}
    for page in doc:
        t = page.get_text("text")
        if "CONTRACTORS SPECIAL POLICY DECLARATIONS" in t and "Named Insured" in t:
            dec_pages["main"] = page
        elif "GENERAL LIABILITY COVERAGE" in t and "APDEC" in t:
            dec_pages["gl"] = page
        elif "PACKAGE ENDORSEMENT" in t and "APDEC" in t:
            dec_pages["lists"] = page
        elif "FORMS INVENTORY" in t and "APDEC" in t:
            dec_pages["forms"] = page

    main = parse_dec_main(dec_pages["main"])
    glp = parse_dec_gl_property(dec_pages["gl"])
    lists = parse_dec_coverage_lists(dec_pages["lists"])
    inv = parse_forms_inventory(dec_pages["forms"])

    # Jacket facts
    jacket = "\n".join("\n".join(reading_order(doc[i])) for i in range(2))
    m = re.search(r"an (Advance Premium Co-operative Property\s+and Casualty Insurance Company)", jacket)
    company_type = clean(m.group(1)) if m else None
    non_assessable = bool(re.search(r"NON-ASSESSABLE\s*POLICY", jacket))

    # Documents from bookmarks
    known_codes = [f["form_number"] for f in inv["forms"]]
    inv_by_key = {code_key(f["form_number"]): f for f in inv["forms"]}
    starts = [(t[1], t[2]) for t in toc if t[0] == 1]
    documents = []
    for k, (title, start) in enumerate(starts):
        end = (starts[k + 1][1] - 1) if k + 1 < len(starts) else doc.page_count
        seq, code, ed, ftitle = parse_bookmark(title, known_codes)
        inv_entry = inv_by_key.get(code_key(code))
        documents.append({
            "sequence": seq,
            "bookmark_title": title,
            "form_number": code,
            "edition": ed,
            "title": ftitle,
            "listed_in_forms_inventory": inv_entry is not None,
            "forms_inventory_sequence": inv_entry["sequence"] if inv_entry else None,
            "page_start": start,
            "page_end": end,
            "page_count": end - start + 1,
            "pages": [build_page(doc[p - 1], code) for p in range(start, end + 1)],
        })
    doc_by_code = {code_key(d["form_number"]): d for d in documents}
    for f in inv["forms"]:
        d = doc_by_code.get(code_key(f["form_number"]))
        f["attached_in_document"] = d is not None
        f["document_sequence"] = d["sequence"] if d else None
        f["edition_matches_attached_form"] = (edition_key(f["edition"]) == edition_key(d["edition"])
                                              if d and d["edition"] else None)

    schedules = {}
    for key, (code, fn) in SCHEDULE_PARSERS.items():
        d = doc_by_code.get(code_key(code))
        if d is None:
            schedules[key] = None
            continue
        inv_entry = inv_by_key.get(code_key(code))
        ref = {"form_number": code, "edition": inv_entry["edition"] if inv_entry else d["edition"],
               "page": d["page_start"]}
        schedules[key] = fn(doc[d["page_start"] - 1], ref)

    carrier = main["carrier"]
    carrier["company_type"] = company_type
    carrier["non_assessable_policy"] = non_assessable

    period_checks = [glp["header"], lists["header"], inv["header"]]
    consistent = all(h["policy_number"] == main["policy_number"]
                     and h["effective_date"] == main["policy_period"]["effective_date"]
                     and h["expiration_date"] == main["policy_period"]["expiration_date"]
                     for h in period_checks)
    missing_text_pages = text_completeness(doc)

    return {
        "schema": SCHEMA,
        "source_document": {
            "file_name": os.path.basename(pdf_path),
            "file_size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "page_count": doc.page_count,
            "pdf_metadata": {
                "format": meta.get("format") or None,
                "title": meta.get("title") or None,
                "author": meta.get("author") or None,
                "subject": meta.get("subject") or None,
                "keywords": meta.get("keywords") or None,
                "creator": meta.get("creator") or None,
                "producer": meta.get("producer") or None,
                "creation_date": pdf_date(meta.get("creationDate")),
                "modification_date": pdf_date(meta.get("modDate")),
                "encrypted": bool(meta.get("encryption")),
            },
            "bookmarks": [{"level": t[0], "title": t[1], "page": t[2]} for t in toc],
        },
        "carrier": carrier,
        "policy": {
            "policy_number": main["policy_number"],
            "policy_type": main["policy_type"],
            "declarations_title": main["declarations_title"],
            "declarations_form": main["declarations_form"],
            "transaction_type": main["transaction_type"],
            "billing_type": main["billing_type"],
            "policy_period": main["policy_period"],
            "class_description": glp["class_description"],
            "insuring_agreement": main["insuring_agreement"],
            "countersignature": main["countersignature"],
        },
        "named_insured": main["named_insured"],
        "agent": main["agent"],
        "premium": {
            "annual_premium": main["annual_premium"],
            "minimum_retained_premium": main["minimum_retained_premium"],
        },
        "general_liability": glp["general_liability"],
        "property": glp["property"],
        "package_endorsement": lists["package_endorsement"],
        "tools_and_equipment_coverage_list": lists["tools_and_equipment_coverage_list"],
        "optional_liability_coverage_list": lists["optional_liability_coverage_list"],
        "form_schedules": schedules,
        "forms_inventory": {"section_title": inv["section_title"], "group": inv["group"], "forms": inv["forms"]},
        "documents": documents,
        "extraction": {
            "extracted_on": datetime.date.today().isoformat(),
            "tool": f"PyMuPDF {pymupdf.__version__}",
            "text_method": ("Visible text layer read per page in reading order; two-column pages are read "
                            "left column then right column within bands separated by full-width lines."),
            "checks": {
                "pages_with_text_mismatch": missing_text_pages,
                "declarations_header_matches_page_1": consistent,
            },
            "conventions": [
                "Monetary/limit cells: 'text' is the value exactly as printed; 'amount' is the numeric value "
                "(null when not a number); 'kind' is one of amount, none ('$None'), included, see_form, "
                "blank (empty cell or fill-in line), not_stated (value not given anywhere), text.",
                "Dates are ISO 8601 (YYYY-MM-DD); the printed MM/DD/YYYY form is kept where a 'text' field exists.",
                "Spelling and typos are kept exactly as printed in the source (e.g. 'Commmercial', "
                "'Valuaton', 'Liabiity', 'OPERATONS').",
                "Every page belongs to exactly one entry in 'documents', which follows the PDF's own bookmarks.",
            ],
        },
    }


if __name__ == "__main__":
    result = build(sys.argv[1])
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"wrote {sys.argv[2]}  pages={result['source_document']['page_count']}  "
          f"documents={len(result['documents'])}  text_mismatch={result['extraction']['checks']['pages_with_text_mismatch']}")
