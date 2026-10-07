"""Page-text index of one policy: locate values on pages, count mentions, extract verbatim sentences."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property
from typing import Iterable, Optional

from policy_compare.ingest import Document, Side


def norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


MIN_QUOTE_WORDS = 4


def quote_key(quote: str) -> str:
    """A model quote as it must appear in the text: spaces normalised, outer quote marks dropped, and a leading or
    trailing ellipsis removed (a quote cut short is checked as a verbatim part of the passage)."""
    q = norm_space(quote).strip(" “”\"'")
    q = re.sub(r"^(\.\.\.|…)\s*", "", q)
    return re.sub(r"\s*(\.\.\.|…)$", "", q).strip(" “”\"'")


def norm_key(s: str) -> str:
    """Lowercase alphanumerics only — used to compare labels and values loosely."""
    return re.sub(r"[^a-z0-9$%.]+", " ", (s or "").lower()).strip()


@dataclass
class Sentence:
    page: int
    text: str


class TextIndex:
    def __init__(self, side: Side):
        self.side = side
        self.pages: dict[int, str] = {}
        self.page_doc: dict[int, Document] = {}
        for doc in side.model.documents:
            for p in doc.pages:
                self.pages[p.page_number] = p.text or ""
                self.page_doc[p.page_number] = doc
        decl = (side.model.policy.model_extra or {}).get("declarations_form") or {}
        self.decl_form = (decl.get("form_number") or "").strip().upper() if isinstance(decl, dict) else ""

    # ---------- documents ----------
    @cached_property
    def declarations_pages(self) -> list[int]:
        """Pages of the declarations document (by declarations_form, else documents titled *DEC*)."""
        out = []
        for doc in self.side.model.documents:
            fn = (doc.form_number or "").strip().upper()
            title = f"{doc.title or ''} {doc.bookmark_title or ''}".upper()
            if (self.decl_form and fn == self.decl_form) or (not self.decl_form and re.search(r"\bDEC(LARATIONS)?\b", title)):
                out.extend(p.page_number for p in doc.pages)
        return out

    def scanned_pages(self) -> list[int]:
        """Pages with images but (almost) no text layer."""
        out = []
        for doc in self.side.model.documents:
            for p in doc.pages:
                if len((p.text or "").strip()) < 20 and p.images:
                    out.append(p.page_number)
        return out

    def document_for_form(self, form_number: str) -> Optional[Document]:
        key = form_key(form_number)
        for doc in self.side.model.documents:
            if form_key(doc.form_number or "") == key:
                return doc
        return None

    def document_text(self, doc: Document) -> str:
        return "\n".join(p.text or "" for p in doc.pages)

    # ---------- locating ----------
    def locate(self, value: Optional[str], label: Optional[str] = None, prefer: Iterable[int] = ()) -> Optional[int]:
        """Page where the value (ideally on the same line as the label) is printed."""
        if not value:
            return None
        v = norm_space(str(value))
        if len(v) < 2:
            return None
        order = list(dict.fromkeys([*prefer, *self.declarations_pages, *sorted(self.pages)]))
        label_words = [w for w in norm_key(label or "").split() if len(w) > 2][:4]
        best_value_only = None
        for pg in order:
            text = self.pages.get(pg, "")
            if v not in norm_space(text):
                continue
            if label_words:
                for line in text.splitlines():
                    nl = norm_key(line)
                    if v in norm_space(line) and sum(w in nl for w in label_words) >= max(1, len(label_words) // 2):
                        return pg
            if best_value_only is None:
                best_value_only = pg
        if best_value_only is None:
            return None
        # value found but never on the label's line: accept it on the declarations or when it is unambiguous
        if not label_words or best_value_only in self.declarations_pages or self._unique(v):
            return best_value_only
        return None

    def locate_label(self, label: str) -> Optional[int]:
        words = [w for w in norm_key(label).split() if len(w) > 2]
        if not words:
            return None
        for pg in list(dict.fromkeys([*self.declarations_pages, *sorted(self.pages)])):
            nl = norm_key(self.pages[pg])
            if all(w in nl for w in words):
                return pg
        return None

    def _unique(self, v: str) -> bool:
        return sum(v in norm_space(t) for t in self.pages.values()) == 1

    # ---------- mentions & quotes ----------
    def count(self, term: str) -> int:
        pat = re.compile(r"\b" + re.escape(term.lower()) + r"\b")
        return sum(len(pat.findall((t or "").lower())) for t in self.pages.values())

    @cached_property
    def sentences(self) -> list[Sentence]:
        out = []
        for pg, text in sorted(self.pages.items()):
            flat = norm_space(text)
            for s in re.split(r"(?<=[.;:])\s+(?=[A-Z(“\"])|\s+-{2,}\s+", flat):
                s = s.strip()
                if 12 <= len(s) <= 400:
                    out.append(Sentence(pg, s))
        return out

    def sentences_with(self, term: str, limit: int = 8, prefer_pages: Iterable[int] = ()) -> list[Sentence]:
        """Sentences containing the term, best first: on preferred pages (the related form itself), readable
        case, no page furniture, a sensible length."""
        pat = re.compile(r"\b" + re.escape(term.lower()) + r"\b")
        prefer = set(prefer_pages)

        def rank(s: Sentence) -> float:
            letters = [c for c in s.text if c.isalpha()]
            upper = sum(c.isupper() for c in letters) / max(1, len(letters))
            score = (3 if s.page in prefer else 0) - (2 if upper > 0.5 else 0)
            score -= 2 if re.search(r"page \d+ of \d+|please read", s.text, re.I) else 0
            score -= 0 if 40 <= len(s.text) <= 240 else 1
            return -score

        hits = sorted((s for s in self.sentences if pat.search(s.text.lower())), key=rank)
        if not hits:   # text without sentence punctuation (schedules, titles): fall back to the matching line
            for pg, text in sorted(self.pages.items()):
                for line in text.splitlines():
                    if pat.search(line.lower()) and len(line.strip()) >= 6:
                        hits.append(Sentence(pg, norm_space(line)))
        return hits[:limit]

    def contains_verbatim(self, quote: str, min_words: int = 1) -> Optional[int]:
        """First page whose text contains the quote. Model quotes pass min_words=MIN_QUOTE_WORDS so that a word or
        two cannot count as a verified quote."""
        q = quote_key(quote)
        if len(q.split()) < min_words:
            return None
        for pg, text in self.pages.items():
            if q and q in norm_space(text):
                return pg
        return None

    def search_any(self, phrases: Iterable[str]) -> list[tuple[int, str]]:
        hits = []
        for pg, text in sorted(self.pages.items()):
            low = text.lower()
            for ph in phrases:
                if ph.lower() in low:
                    hits.append((pg, ph))
        return hits


def form_key(form_number: str) -> str:
    """'CG 20 37' / 'CG2037' / 'cg 20-37' -> 'CG2037'."""
    return re.sub(r"[^A-Z0-9]", "", (form_number or "").upper())


def edition_key(edition: Optional[str]) -> str:
    """'04 23' / '04-23' / '0423' -> '04/23'; '1.2' stays '1.2'."""
    e = (edition or "").strip()
    if not e:
        return ""
    if re.fullmatch(r"\d+(\.\d+)+", e):
        return e
    digits = re.sub(r"\D", "", e)
    if len(digits) == 4:
        return f"{digits[:2]}/{digits[2:]}"
    return e.replace(" ", "/").replace("-", "/")
