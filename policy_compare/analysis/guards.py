"""Guards applied after the model: facts stay facts; risk stays between the rubric floor and what the changes support."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from policy_compare.analysis.narrative import PRIMARY, SECTION_ORDER, floor_risk, max_level, min_level

if TYPE_CHECKING:
    from policy_compare.engine import Analysis

_NUM = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?|\d{1,2}/\d{1,2}/\d{2,4}")


def allowed_numbers(an: "Analysis") -> set[str]:
    """Every number/date the narrative may mention: anything shown in a finding, KPI-derived values, counts."""
    vals: set[str] = set()
    for f in an.all_findings():
        for s in (f.exp, f.ren, f.change, f.label, f.why or ""):
            vals.update(_NUM.findall(s or ""))
        for v in f.context.values():
            if isinstance(v, (int, float)):
                vals.add(f"{v:,.0f}".replace(",", ""))
            elif isinstance(v, str):
                vals.update(_NUM.findall(v))            # e.g. "within 45 days" in a form excerpt the model read
            elif isinstance(v, list):
                for x in v:
                    vals.update(_NUM.findall(str(x)))
    for forms in (an.fe, an.fr):
        for fm in forms.values():
            vals.update(_NUM.findall(f"{fm.number} {fm.edition} {fm.title}"))
    vals.update(str(n) for n in range(0, 61))
    vals.add(an.deadline)
    return {re.sub(r"[,$%]", "", v) for v in vals}


def unsupported_numbers(text: str, allowed: set[str]) -> list[str]:
    bad = []
    for m in _NUM.findall(text or ""):
        core = re.sub(r"[,$%]", "", m)
        if core not in allowed and not any(core in a for a in allowed):
            bad.append(m)
    return bad


def ceiling_risk(rows) -> str:
    """Highest risk the facts can support: administrative-only changes are low; without any reduction or
    high-severity confirmation a section cannot be high."""
    material = [r for r in rows if r.changed and r.impact != "no_impact"]
    if not material:
        return "low"
    if any(r.impact == "reduced" for r in material) or any(r.impact == "confirm" and r.severity in ("critical", "high") for r in material):
        return "high"
    return "medium"


def apply_guards(an: "Analysis") -> None:
    log = an.audit.setdefault("guards", [])
    # 1. section risk: rubric floor, and a ceiling so the model cannot alarm on administrative changes
    for s in SECTION_ORDER:
        n = an.narratives.sections.get(s)
        if not n:
            continue
        rows = an.findings.get(s, [])
        floor = floor_risk(rows) if s in PRIMARY else "low"
        ceiling = ceiling_risk(rows)
        new = min_level(max_level(n.risk_level, floor), ceiling)
        if new != n.risk_level:
            log.append(f"section {s}: risk {n.risk_level} -> {new} (floor {floor}, ceiling {ceiling})")
            n.risk_level = new
    # 2. overall risk: at least the highest primary section, at most what the changes support
    ex = an.narratives.executive
    if ex:
        top = "low"
        for s in PRIMARY:
            if s in an.narratives.sections:
                top = max_level(top, an.narratives.sections[s].risk_level)
        new = min_level(max_level(ex.risk, top), ceiling_risk(an.unique_changes()))
        if new != ex.risk:
            log.append(f"overall risk {ex.risk} -> {new}")
            ex.risk = new
    # 3. critical list only holds real candidates
    valid = {f.id for f in an.critical_candidates()}
    dropped = [i for i in an.narratives.critical_order if i not in valid]
    if dropped:
        log.append(f"critical list: dropped unknown ids {dropped}")
        an.narratives.critical_order = [i for i in an.narratives.critical_order if i in valid]


_MD = re.compile(r"[*_`#>]+")
_REMOVAL = re.compile(r"\b(remov\w*|delet\w*|dropp\w*|eliminat\w*|no longer|withdrawn|taken off)\b", re.I)
_ADDITION = re.compile(r"\b(added|adds|new on|newly|introduc\w*)\b", re.I)
_REDUCTION = re.compile(r"\b(narrow\w*|reduc\w*|restrict\w*|lost|loses|less cover|decreas\w*|worse|weaker)\b", re.I)
_EDITION = re.compile(r"\beditions?\b", re.I)
_NEGATION = re.compile(r"\b(no|not|nothing|none|without|never|neither|nor)\b", re.I)
# talk about the comparison itself instead of the policies ("values directly from comparison report", "quotes match")
_META = re.compile(r"\b(comparison report|from (the )?comparison|quotes? match\w*|match(es)? closely|changes? (are |were )?"
                   r"confirmed|confirmed except|(per|from|in) the (facts|data|json|input)|(provided|given) (facts|data)|"
                   r"no missing data)\b", re.I)
# claims that something changed or is affected (used where nothing material changed)
_CHANGE_CLAIM = re.compile(r"\b(minor changes?|changes? (noted|found|made|identified|observed)|(is|are|was|were) affected|"
                           r"affects?|affected by)\b", re.I)
_JARGON = ["fixed by rules", "fixed_by_rules", "json", "item id", "schema"]


class ClaimChecker:
    """Rejects model sentences that contradict the forms schedule, e.g. 'communicable disease exclusion removed'
    when a form with exactly that subject is still on the renewal (only its notice was removed)."""

    def __init__(self, an: "Analysis"):
        from policy_compare.forms import title_words
        self._tw = title_words
        self.on_renewal = {k: f for k, f in an.fr.items()}
        self.on_expiring = {k: f for k, f in an.fe.items()}
        self.both = {k: f for k, f in an.fe.items() if k in an.fr}
        self.editions = any(f.kind == "form_edition" for f in an.findings.get("forms", []))
        from policy_compare.forms import _number_pattern
        status = {k: "both" for k in an.fe if k in an.fr} | {k: "removed" for k in an.fe if k not in an.fr} | \
                 {k: "added" for k in an.fr if k not in an.fe}
        forms = {**an.fe, **an.fr}
        self.numbers = [(forms[k].number, _number_pattern(forms[k].number), st) for k, st in status.items()
                        if re.search(r"[A-Za-z]", forms[k].number)]
        # words like "removal" inside form titles ("Snow Removal Operations") are names, not claims
        self.title_verbs = {m.group(0).lower() for f in forms.values() for m in _REMOVAL.finditer(f.title or "")} | \
                           {m.group(0).lower() for f in forms.values() for m in _ADDITION.finditer(f.title or "")}
        words = set()
        for f in list(an.fe.values()) + list(an.fr.values()):
            words |= {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", f.title)}
        for f in an.all_findings():
            words |= {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", f.label)}
        for name in (an.E.insured, an.R.insured, an.E.carrier, an.R.carrier):
            words |= {w.lower() for w in re.findall(r"[A-Za-z]+", name or "")}
        self.proper = words
        # real names (form titles, parties) keep their capitals when Title Case text is put into sentence case
        self.names = sorted({x for f in list(an.fe.values()) + list(an.fr.values()) for x in (f.title, f.number) if x} |
                            {x for x in (an.E.insured, an.R.insured, an.E.carrier, an.R.carrier) if x}, key=len, reverse=True)

    def problem(self, text: str, no_reduction: bool = False) -> str | None:
        for sent in re.split(r"(?<=[.;!?])\s+", text):
            low = sent.lower()
            if no_reduction and _REDUCTION.search(low) and not _NEGATION.search(low):
                return "claims cover was reduced, but no change in scope reduces cover"
            if not self.editions and _EDITION.search(low) and not _NEGATION.search(low):
                return "mentions a form edition, but no form edition changed"
            by_number = self._number_claim(sent)
            if by_number:
                return by_number
            words = self._tw(sent)
            if _REMOVAL.search(low) and "notice" not in low:
                for f in self.on_renewal.values():
                    fw = self._tw(f.title)
                    if len(fw) >= 2 and fw <= words and _REMOVAL.search(_outside_title(low, f.title)):
                        return f"says '{f.title}' was removed, but {f.number} is still on the renewal"
            if _ADDITION.search(low):
                for f in self.both.values():
                    fw = self._tw(f.title)
                    if len(fw) >= 2 and fw <= words:
                        return f"says '{f.title}' was added, but {f.number} is on both policies"
        return None

    def _number_claim(self, sent: str) -> str | None:
        """'AP 0853UF was removed' when AP 0853UF is still on the renewal; 'X was added' when X is on both policies.
        A form named before the verb is the subject; a sentence that also names a form really removed (or added) is
        left alone ('CP 382 was removed and replaced by ...')."""
        hits = [(m.start(), num, st) for num, pat, st in self.numbers for m in pat.finditer(sent)]
        if not hits:
            return None
        text = _mask(sent, self.title_verbs)
        for verb, real, wrong in ((_REMOVAL, "removed", {"both": "it is still on the renewal", "added": "it is new on the renewal"}),
                                  (_ADDITION, "added", {"both": "it is on both policies", "removed": "it was removed"})):
            v = verb.search(text)
            if not v or _NEGATION.search(text[max(0, v.start() - 12): v.start()]) or any(st == real for _, _, st in hits):
                continue
            subject = [(num, st) for pos, num, st in hits if pos < v.start() and st in wrong]
            if subject:
                num, st = subject[0]
                return f"says {num} was {real}, but {wrong[st]}"
        return None


def _mask(text: str, words: set[str]) -> str:
    """Blank out the given words (same length, so positions stay valid)."""
    for w in words:
        text = re.sub(r"\b" + re.escape(w) + r"\b", lambda m: " " * len(m.group(0)), text, flags=re.I)
    return text


def _outside_title(low: str, title: str) -> str:
    """The sentence without the removal-like words of a form title ('Snow Removal Operations'), so that naming
    the form is not read as saying it was removed."""
    for w in {m.group(0).lower() for m in _REMOVAL.finditer(title)}:
        low = re.sub(r"\b" + re.escape(w) + r"\b", " ", low)
    return low


def _sentence_case(t: str, names: list[str] = ()) -> str:
    """'Pending Carrier Confirmation Required' -> 'Pending carrier confirmation required'. Acronyms, words with digits
    and words inside a known name (form title, insured, carrier) keep their capitals."""
    keep = set()
    for name in names:
        words = re.findall(r"[A-Za-z0-9]+", name)
        if len(words) < 1:
            continue
        pat = r"\b" + r"\W+".join(map(re.escape, words)) + r"\b"
        for m in re.finditer(pat, t, flags=re.I):
            keep |= set(range(m.start(), m.end()))
    out, first = [], True
    for m in re.finditer(r"\S+", t):
        w = m.group(0)
        core = re.sub(r"[^A-Za-z'\-]", "", w)
        fixed = first or not core or core.isupper() or any(c.isdigit() for c in w) or m.start() in keep
        out.append(w if fixed else w.lower())
        first = False
    return " ".join(out)


def _title_case(t: str, proper=frozenset()) -> bool:
    """True for 'Pending Confirmation'-style labels. Words of known form titles, labels and party names are proper
    nouns ('NY Anti Arson Amendment') and do not count."""
    long = [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", t)
            if len(w) > 3 and not w.isupper() and w.lower() not in proper]
    return len(long) >= 3 and sum(w[0].isupper() for w in long) / len(long) > 0.7


def check_text(text, max_words: int, allowed: set[str] | None = None, forbid: list[str] | None = None,
               checker: ClaimChecker | None = None, kind: str = "text",
               no_reduction: bool = False, no_change: bool = False) -> tuple[str | None, str | None]:
    """Validate one piece of model text. Returns (clean text, None) or (None, reason) when the default must be used.
    kind: 'headline' must read as a sentence; 'bullet' must not be Title Case; 'text' is free prose."""
    if not text or not isinstance(text, str):
        return None, "empty"
    if kind == "letter":   # keep line breaks (letters, emails), tidy spaces within lines
        t = "\n".join(re.sub(r"[ \t]+", " ", ln).strip() for ln in text.replace("\r", "").split("\n")).strip()
        t = re.sub(r"\n{3,}", "\n\n", t)
    else:
        t = re.sub(r"\s+", " ", _MD.sub("", text)).strip().strip('"').strip()
    if not t:
        return None, "empty"
    if len(t.split()) > max_words * 1.6:
        return None, f"too long ({len(t.split())} words, limit {max_words})"
    if allowed is not None:
        bad = unsupported_numbers(t, allowed)
        if bad:
            return None, f"numbers not in the facts: {', '.join(bad)}"
    if forbid:
        hit = next((p for p in forbid if p.lower() in t.lower()), None)
        if hit:
            return None, f"forbidden phrase '{hit}'"
    if _META.search(t):
        return None, "talks about the comparison, not the policies"
    if no_change and _CHANGE_CLAIM.search(t) and not _NEGATION.search(t):
        return None, "contradicts the facts: claims a change, but nothing material changed here"
    if checker:
        why = checker.problem(t, no_reduction=no_reduction)
        if why:
            return None, f"contradicts the facts: {why}"
    if kind in ("headline", "bullet") and _title_case(t, checker.proper if checker else frozenset()):
        t = _sentence_case(t, checker.names if checker else [])     # keep the content, fix the style
    if kind == "headline" and len(t.split()) < 4:
        return None, "headline too short to be a sentence"
    if kind == "bullet" and len(t.split()) < 3:
        return None, "bullet too short to be a sentence"
    if kind in ("headline", "bullet") and t[-1] not in ".!?":
        t += "."
    return t, None


def clean_text(text, max_words: int, allowed: set[str] | None = None, forbid: list[str] | None = None,
               checker: ClaimChecker | None = None, kind: str = "text"):
    """check_text without the reason (kept for simple callers)."""
    return check_text(text, max_words, allowed, forbid, checker, kind)[0]


class TextGate:
    """check_text bound to one analysis; every rejection is written to the audit with its reason."""

    def __init__(self, an: "Analysis"):
        self.an = an
        self.allowed = allowed_numbers(an)
        self.checker = ClaimChecker(an)

    def take(self, field: str, text, max_words: int, kind: str = "text", forbid: list[str] | None = None,
             no_reduction: bool = False, no_change: bool = False):
        clean, reason = check_text(text, max_words, self.allowed, (forbid or []) + _JARGON, self.checker, kind,
                                   no_reduction, no_change)
        if reason and reason != "empty":
            self.an.audit.setdefault("rejected_text", []).append({"field": field, "text": text, "reason": reason})
        return clean
