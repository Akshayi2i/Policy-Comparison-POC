"""E: questions about the two policies ("Is snow removal covered?").

Passages are retrieved from both policies by code; the model answers only from them and must cite verbatim quotes,
which are checked and given page references. Without the model the best passages are returned as they are.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from policy_compare.ingest import load_pair
from policy_compare.settings import settings
from policy_compare.textindex import MIN_QUOTE_WORDS, TextIndex

STOP = {"the", "and", "for", "are", "was", "with", "this", "that", "what", "which", "does", "did", "have", "has", "from",
        "policy", "policies", "covered", "cover", "coverage", "there", "any", "our", "your", "their", "will", "can", "how",
        "under", "into", "about", "is", "it", "in", "on", "of", "a", "an", "to", "be", "do", "or", "we", "my", "if"}
K = 6


def _terms(question: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9][a-z0-9\-]+", question.lower()) if w not in STOP and len(w) > 2]


def retrieve(ti: TextIndex, question: str, k: int = K) -> list[dict]:
    terms = _terms(question)
    if not terms:
        return []
    bigrams = [f"{a} {b}" for a, b in zip(terms, terms[1:])]
    scored = []
    for s in ti.sentences:
        low = s.text.lower()
        score = sum(1 for t in terms if t in low) + sum(2 for b in bigrams if b in low)
        if score:
            scored.append((score, -len(s.text), s))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out, seen = [], set()
    for score, _, s in scored:
        key = s.text[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append({"page": s.page, "text": s.text[:400], "score": score})
        if len(out) >= k:
            break
    return out


def ask(a, b, question: str, use_llm: Optional[bool] = None) -> dict:
    E, R, _ = load_pair(a, b)
    ti = {"expiring": TextIndex(E), "renewal": TextIndex(R)}
    ref = {"expiring": E.ref, "renewal": R.ref}
    passages = {side: retrieve(t, question) for side, t in ti.items()}
    result = {"question": question, "expiring": E.file_name, "renewal": R.file_name,
              "passages": [{"side": side, "ref": f"{ref[side]} p.{p['page']}", "text": p["text"]}
                           for side in ("expiring", "renewal") for p in passages[side]]}
    want = settings().llm_enabled if use_llm is None else (use_llm and settings().llm_enabled)
    if not want:
        result.update({"answer": None, "mode": "retrieval only (model not configured)", "citations": [], "verified": False})
        return result
    if not any(passages.values()):
        result.update({"answer": "The policy text does not appear to mention this.", "mode": "model", "citations": [],
                       "verified": False, "confidence": "low"})
        return result

    import json

    from policy_compare.analysis.guards import _NUM, check_text
    from policy_compare.llm.client import LLMClient, LLMError, prompt
    from policy_compare.schema.llm_io import QAAnswer
    llm = LLMClient()
    shown = {side: [{"page": p["page"], "text": p["text"]} for p in passages[side]] for side in passages}
    try:
        res = llm.structured("qa", prompt("qa").format(question=question, passages=json.dumps(shown, ensure_ascii=False, indent=1)),
                             QAAnswer)
    except LLMError as e:
        result.update({"answer": None, "mode": f"model failed: {e}", "citations": [], "verified": False})
        return result
    cites = []
    for c in res.citations:
        page = ti[c.side].contains_verbatim(c.quote, MIN_QUOTE_WORDS)
        if page:
            cites.append({"side": c.side, "ref": f"{ref[c.side]} p.{page}", "quote": c.quote.strip().strip("“”\"")})
    allowed = {re.sub(r"[,$%]", "", n) for p in sum(passages.values(), []) for n in _NUM.findall(p["text"])}
    allowed |= {re.sub(r"[,$%]", "", n) for n in _NUM.findall(question)} | {str(i) for i in range(0, 13)}
    answer, reason = check_text(res.answer, 120, allowed=allowed)
    if not cites or not answer:
        result.update({"answer": "The retrieved policy text does not clearly answer this; see the passages below.",
                       "mode": "model", "citations": cites, "verified": False, "confidence": "low",
                       "rejected": reason or "no verbatim citation"})
        return result
    result.update({"answer": answer, "mode": "model", "citations": cites, "verified": True, "confidence": res.confidence,
                   "model": settings().llm_model})
    return result
