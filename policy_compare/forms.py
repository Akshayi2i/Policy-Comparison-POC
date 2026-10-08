"""Forms & endorsements: schedule comparison (number / edition), wording diff, contract-slot matching."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Optional

from policy_compare.diff import Ids, why
from policy_compare.findings import Finding
from policy_compare.ingest import Document, Side
from policy_compare.settings import config
from policy_compare.textindex import TextIndex, edition_key, form_key, norm_space


@dataclass
class Form:
    number: str
    edition: str
    title: str
    key: str
    page: Optional[int] = None          # schedule page (where the form is listed)
    doc: Optional[Document] = None
    attached: Optional[bool] = None
    edition_matches: Optional[bool] = None
    slots: list[str] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)   # keys of other forms this form's own text names
    role: Optional[str] = None          # coverage | exclusion | notice | condition | schedule (read from the text by the model)

    @property
    def display(self) -> str:
        return f"{self.number} {self.edition}".strip()

    @property
    def full_title(self) -> str:
        return f"{self.number} — {self.title}" if self.title else self.number


def collect_forms(side: Side, ti: TextIndex) -> dict[str, Form]:
    """The forms schedule of one policy: forms_inventory if present, else attached documents with form numbers."""
    out: dict[str, Form] = {}
    inv = (side.data.get("forms_inventory") or {}).get("forms") or []
    skip = {ti.decl_form} if ti.decl_form else set()
    if inv:
        for it in inv:
            num = (it.get("form_number") or "").strip()
            if not num:
                continue
            f = Form(number=num, edition=edition_key(it.get("edition")), title=(it.get("title") or "").strip(),
                     key=form_key(num), attached=it.get("attached_in_document"),
                     edition_matches=it.get("edition_matches_attached_form"))
            out[f.key] = f
    else:
        for doc in side.model.documents:
            num = (doc.form_number or "").strip()
            if num and num.upper() not in skip and doc.sequence != 1:
                out[form_key(num)] = Form(number=num, edition=edition_key(doc.edition), title=(doc.title or "").strip(), key=form_key(num))
    for f in out.values():
        f.doc = ti.document_for_form(f.number)
        f.page = ti.locate(f.number, f.title.split()[0] if f.title else None, prefer=ti.declarations_pages) or (f.doc.page_start if f.doc else None)
        f.slots = match_slots(f)
    link_forms(out, ti)
    return out


MAX_MENTIONS = 5     # a document naming more forms than this is a schedule, not an endorsement that amends them


def _number_pattern(number: str) -> re.Pattern:
    """'AP 0853UF' also matches 'AP-0853UF' / 'AP0853UF', but not inside a longer code."""
    parts = re.findall(r"[A-Za-z]+|\d+", number)
    return re.compile(r"(?<![A-Za-z0-9])" + r"[\s\-]*".join(map(re.escape, parts)) + r"(?![A-Za-z0-9])", re.I)


def _plain(text: str) -> str:
    return " " + " ".join(re.findall(r"[a-z0-9]+", (text or "").lower())) + " "


def link_forms(forms: dict[str, Form], ti: TextIndex) -> None:
    """Record which forms each form's own text names, by form number or by full title (3+ words). An endorsement
    that names another form usually amends it, e.g. a buy-back endorsement that narrows an exclusion."""
    pats = {k: _number_pattern(f.number) for k, f in forms.items() if re.search(r"[A-Za-z]", f.number)}
    titles = {k: _plain(f.title) for k, f in forms.items() if len(_plain(f.title).split()) >= 3}
    for k, f in forms.items():
        if not f.doc:
            continue
        text = ti.document_text(f.doc)
        plain = _plain(text)
        hits = [o for o in forms if o != k and ((o in pats and pats[o].search(text)) or (o in titles and titles[o] in plain))]
        f.mentions = hits if len(hits) <= MAX_MENTIONS else []


def amended_by(form: Form, forms: dict[str, Form]) -> list[Form]:
    """Forms on the same policy whose text names this form."""
    return [o for o in forms.values() if form.key in o.mentions]


def link_context(form: Form, own: dict[str, Form], other: dict[str, Form]) -> dict:
    """Facts for the model: the forms this form names and the forms that name it, with where each one stands."""
    def status(key: str) -> str:
        return "on both policies" if key in own and key in other else ("only on this policy" if key in own else "not on this policy")
    names = [f"{own[k].full_title} ({status(k)})" for k in form.mentions if k in own]
    by = [f"{o.full_title} ({status(o.key)})" for o in amended_by(form, own)]
    return {k: v for k, v in (("forms_this_form_amends", names), ("forms_that_amend_this_form", by)) if v}


def _title_has(title: str, words: list[str]) -> bool:
    t = title.lower()
    return all(w.lower() in t for w in words)


def match_slots(form: Form) -> list[str]:
    out = []
    for slot in config("contract_slots").get("slots", []):
        if form.key in {form_key(x) for x in slot.get("forms", [])}:
            out.append(slot["id"])
            continue
        if any(_title_has(form.title, ws) for ws in slot.get("title_any", [])) and not any(
                w.lower() in form.title.lower() for w in slot.get("exclude_title", [])):
            out.append(slot["id"])
    return out


# sentences that say what a form does (as opposed to headers, addresses and boilerplate)
_OPERATIVE = re.compile(r"\b(we do not pay|we will not pay|does not apply|do not apply|not cover|is added|are added|"
                        r"is deleted|is replaced|replaces|is amended|are amended|requires?|required|must|shall|excluded|"
                        r"exclusion applies|applies to|apply to|is covered|we will pay|we cover|includes)\b", re.I)
_BOILERPLATE = re.compile(r"(please read|this endorsement (changes|modifies)|all other terms|copyright|p\.?o\.? box|"
                          r"\btel\b|\bfax\b|page \d+ of \d+)", re.I)


# a sentence that only introduces the operative text ("... is amended as follows:", "This insurance does not apply to:")
_INTRO = re.compile(r"(:\s*$|as follows|the following (is|are) (added|amended|deleted|replaced)|\b\d+\.\s*$)", re.I)
_HEADING_WORD = re.compile(r"[A-Z0-9][A-Z0-9&/'\-,().]*|[-–—]+")


def form_summary(ti: TextIndex, doc: Optional[Document], max_words: int = 34) -> Optional[tuple[str, int]]:
    """The form's first operative sentence, verbatim (clipped), with its page: what the form does in its own words."""
    if not doc:
        return None
    pages = {p.page_number for p in doc.pages}
    for s in ti.sentences:
        if s.page not in pages or _BOILERPLATE.search(s.text) or _INTRO.search(s.text):
            continue
        words = s.text.split()
        while words and _HEADING_WORD.fullmatch(words[0]):   # drop a heading in capitals before the sentence
            words = words[1:]
        if len(words) < 7 or not _OPERATIVE.search(" ".join(words)):
            continue
        text = " ".join(words[:max_words]) + ("…" if len(words) > max_words else "")
        return text, s.page
    return None


def _what_it_does(side: Side, ti: TextIndex, f: Form, template: str) -> str:
    s = form_summary(ti, f.doc)
    return f"The form says: “{s[0]}” [[{side.ref} p.{s[1]}]]. {template}" if s else template


def _ref(side: Side, page: Optional[int]) -> Optional[str]:
    return f"{side.ref} p.{page}" if page else None


def _excerpt(ti: TextIndex, doc: Optional[Document], limit: int = 900) -> str:
    if not doc:
        return ""
    return norm_space(ti.document_text(doc))[:limit]


def _words_in(title: str, words: list[str]) -> bool:
    t = title.lower()
    return any(re.search(r"\b" + re.escape(w) + r"\b", t) for w in words)


TITLE_STOP = {"form", "forms", "policy", "coverage", "coverages", "endorsement", "endorsements", "important",
              "the", "and", "with", "under", "than", "more", "other", "for", "from", "this"}


def title_words(title: str) -> set[str]:
    """Significant words of a form title, singularised ('Exclusions' -> 'exclusion')."""
    words = set()
    for w in re.findall(r"[a-z][a-z\-]+", (title or "").lower()):
        w = w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w
        if len(w) >= 4 and w not in TITLE_STOP:
            words.add(w)
    return words


def related_forms(form: Form, others: dict[str, Form], exclude: set[str]) -> list[str]:
    """Forms on the other policy that cover the same subject (two or more significant title words in common)."""
    mine = title_words(form.title)
    hits = [(len(mine & title_words(o.title)), o) for k, o in others.items() if k not in exclude and k != form.key]
    return [f"{o.number} — {o.title}" for n, o in sorted(hits, key=lambda x: -x[0]) if n >= 2][:4]


ROLE_TEXT = {
    "notice": "notice or disclosure (informational; does not by itself change cover)",
    "exclusion": "exclusion or limitation (narrows cover)",
    "coverage": "coverage grant or buy-back (adds or gives back cover)",
    "condition": "condition or amendatory form",
    "schedule": "declarations or schedule",
}


def role_key(form: Form, rub: dict) -> Optional[str]:
    """The model's reading of the form text when available, else a guess from the title (None = not known)."""
    if form.role:
        return form.role
    if _words_in(form.title, rub["notice_words"]):
        return "notice"
    if _words_in(form.title, rub["exclusion_words"]):
        return "exclusion"
    return None


def form_role(form: Form, rub: dict) -> str:
    k = role_key(form, rub)
    return ROLE_TEXT[k] if k else "coverage, condition or amendatory form"


def form_default_impact(form: Form, direction: str, rub: dict) -> tuple[dict, bool]:
    """Rubric impact of a form that is only on one policy, by its role. Returns (impact/severity, locked)."""
    k = role_key(form, rub)
    if direction == "removed":
        if form.slots:
            return rub["removed_contract_slot"], True
        if k == "notice" or k == "schedule":
            return {"impact": "no_impact", "severity": "low"}, False
        if k == "exclusion":
            return {"impact": "improved", "severity": "low"}, False
        if k == "coverage":
            return rub["removed_coverage"], False
        return rub["removed_default"], False
    if k == "notice" or k == "schedule":
        return {"impact": "no_impact", "severity": "low"}, False
    if k == "exclusion":
        return rub["added_exclusion"], False
    if k == "coverage":
        return rub["added_coverage"], False
    return rub["added_default"], False


def compare_forms(E: Side, R: Side, ti_e: TextIndex, ti_r: TextIndex, ids: Ids) -> tuple[list[Finding], list[Form], dict, dict]:
    """Returns (findings, unchanged forms, expiring forms, renewal forms)."""
    rub = config("rubric")["forms"]
    fe, fr = collect_forms(E, ti_e), collect_forms(R, ti_r)
    out: list[Finding] = []
    unchanged: list[Form] = []

    for key, f in fe.items():
        if key in fr:
            continue
        r, locked = form_default_impact(f, "removed", rub)
        out.append(Finding(id=ids.next(), change_key=f"form:{key}", section="forms", kind="form_removed", label=f.full_title,
                           group="removed", exp=f.display, ren="Not on policy", exp_ref=_ref(E, f.page), change="Removed at renewal",
                           impact=r["impact"], severity=r["severity"], locked=locked,
                           why=_what_it_does(E, ti_e, f, why("form_removed")),
                           explain_label="What it does", type="form",
                           context={"form_number": f.number, "title": f.title, "form_role": form_role(f, rub),
                                    "contract_slots": f.slots,
                                    "related_forms_still_on_renewal": related_forms(f, fr, exclude=set()),
                                    **link_context(f, fe, fr),
                                    "form_text_excerpt": _excerpt(ti_e, f.doc)}))
    for key, f in fr.items():
        if key in fe:
            continue
        r, locked = form_default_impact(f, "added", rub)
        out.append(Finding(id=ids.next(), change_key=f"form:{key}", section="forms", kind="form_added", label=f.full_title,
                           group="added", exp="Not on policy", ren=f.display, ren_ref=_ref(R, f.page), change="Added on renewal",
                           impact=r["impact"], severity=r["severity"], locked=locked,
                           why=_what_it_does(R, ti_r, f, why("form_added")),
                           explain_label="What it does", type="form",
                           context={"form_number": f.number, "title": f.title, "form_role": form_role(f, rub),
                                    "contract_slots": f.slots,
                                    "related_forms_on_expiring": related_forms(f, fe, exclude=set()),
                                    **link_context(f, fr, fe),
                                    "form_text_excerpt": _excerpt(ti_r, f.doc)}))
    for key, a in fe.items():
        b = fr.get(key)
        if not b:
            continue
        if a.edition and b.edition and a.edition != b.edition:
            r = rub["edition_changed"]
            out.append(Finding(id=ids.next(), change_key=f"form:{key}", section="forms", kind="form_edition", label=a.full_title,
                               group="edition", exp=a.display, ren=b.display, exp_ref=_ref(E, a.page), ren_ref=_ref(R, b.page),
                               change=f"Edition {a.edition} → {b.edition}", impact=r["impact"], severity=r["severity"],
                               locked=False, why=why("form_edition"), explain_label="What it does", type="form",
                               context={"form_number": a.number, "title": a.title, "contract_slots": a.slots,
                                        "form_text_excerpt": _excerpt(ti_r, b.doc)}))
            continue
        w = wording_change(a, b, ti_e, ti_r, E, R)
        if w:
            r = rub["wording_changed"]
            exp_snip, ren_snip, pe, pr = w
            out.append(Finding(id=ids.next(), change_key=f"form:{key}:wording", section="forms", kind="form_wording",
                               label=a.full_title, group="wording", exp="Present", ren="Present", exp_ref=_ref(E, pe or a.page),
                               ren_ref=_ref(R, pr or b.page), change=f"“{exp_snip}” → “{ren_snip}”", impact=r["impact"],
                               severity=r["severity"], locked=False, explain_label="Wording", type="form",
                               why=f"expiring “{exp_snip}” [[{_ref(E, pe or a.page)}]] → renewal “{ren_snip}” [[{_ref(R, pr or b.page)}]]. "
                                   + why("form_wording"),
                               context={"form_number": a.number, "title": a.title, "expiring_passage": exp_snip,
                                        "renewal_passage": ren_snip}))
            continue
        unchanged.append(b)
    return out, unchanged, fe, fr


_TOKEN = re.compile(r"[A-Za-z0-9$%]+(?:[.,'’][A-Za-z0-9]+)*")


def _tokens(text: str, extra: list[str]) -> list[tuple[str, int, int]]:
    """Word tokens of the original text as (compare_key, start, end). Tokens inside masked spans (dates, years,
    page markers, policy numbers) compare as a placeholder, so renewal roll-overs are not wording changes."""
    masked: list[tuple[int, int]] = []
    for pat in config("catalogue").get("wording_mask", []):
        masked += [(m.start(), m.end()) for m in re.finditer(pat, text, flags=re.I)]
    for v in extra:
        if v:
            masked += [(m.start(), m.end()) for m in re.finditer(re.escape(v), text, flags=re.I)]
    out = []
    for m in _TOKEN.finditer(text):
        key = "‹x›" if any(a <= m.start() < b for a, b in masked) else m.group(0).lower()
        if out and key == "‹x›" and out[-1][0] == "‹x›":
            out[-1] = ("‹x›", out[-1][1], m.end())
            continue
        out.append((key, m.start(), m.end()))
    return out


def _span(text: str, toks: list, i: int, j: int) -> str:
    if not toks:
        return ""
    i, j = max(0, i), min(len(toks), j)
    if j <= i:
        return ""
    return norm_space(text[toks[i][1]: toks[j - 1][2]])


def wording_change(a: Form, b: Form, ti_e: TextIndex, ti_r: TextIndex, E: Side, R: Side) -> Optional[tuple[str, str, Optional[int], Optional[int]]]:
    """Return the largest differing passage (expiring, renewal, pages) for the same form, ignoring dates/numbers
    that roll over at renewal; None when the text is the same. Passages are verbatim slices of the source."""
    if not (a.doc and b.doc):
        return None
    if ti_e.decl_form and a.key == form_key(ti_e.decl_form):
        return None
    extra = [E.model.policy.policy_number or "", R.model.policy.policy_number or ""]
    ta, tb = ti_e.document_text(a.doc), ti_r.document_text(b.doc)
    wa, wb = _tokens(ta, extra), _tokens(tb, extra)
    ka, kb = [t[0] for t in wa], [t[0] for t in wb]
    if ka == kb:
        return None
    sm = difflib.SequenceMatcher(None, ka, kb, autojunk=False)
    ops = [op for op in sm.get_opcodes() if op[0] != "equal"]
    if not ops:
        return None
    tag, i1, i2, j1, j2 = max(ops, key=lambda o: max(o[2] - o[1], o[4] - o[3]))
    ctx = 2
    exp_snip = _span(ta, wa, i1 - ctx, i2 + ctx)
    ren_snip = _span(tb, wb, j1 - ctx, j2 + ctx)
    pe = ti_e.contains_verbatim(_span(ta, wa, i1 - ctx, i2 + ctx)) if wa else None
    pr = ti_r.contains_verbatim(_span(tb, wb, j1 - ctx, j2 + ctx)) if wb else None
    return _clip(exp_snip), _clip(ren_snip), pe or (a.doc.page_start if a.doc else None), pr or (b.doc.page_start if b.doc else None)


def _clip(s: str, n: int = 110) -> str:
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"
