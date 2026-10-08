"""What each form does — coverage grant, exclusion, notice, condition or schedule — read from its text by the model.

Without the model the role is guessed from words in the title (config/rubric.yaml). The role sets the default impact
of a form that is only on one policy (a removed buy-back reduces cover; a removed notice does not) and decides which
unchanged forms are reviewed as coverage observations.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from policy_compare.forms import form_default_impact, form_role, form_summary, role_key
from policy_compare.llm.client import LLMClient, LLMError, prompt
from policy_compare.schema.llm_io import FormRoles
from policy_compare.settings import config
from policy_compare.textindex import norm_space

if TYPE_CHECKING:
    from policy_compare.engine import Analysis

EXCERPT_CHARS = 350


def _item(i: int, f, ti) -> dict:
    s = form_summary(ti, f.doc)
    return {"id": f"F{i}", "form_number": f.number, "title": f.title,
            "excerpt": norm_space(ti.document_text(f.doc))[:EXCERPT_CHARS] if f.doc else "",
            "key_sentence": s[0] if s else None}


def classify_forms(llm: LLMClient, an: "Analysis") -> None:
    rub = config("rubric")["forms"]
    keys = list(dict.fromkeys(list(an.fr) + list(an.fe)))
    forms = [(k, an.fr.get(k) or an.fe[k], an.ti_r if k in an.fr else an.ti_e) for k in keys]
    items = [_item(i, f, ti) for i, (k, f, ti) in enumerate(forms, 1)]
    try:
        res = llm.structured("form_roles", prompt("formroles").format(forms=json.dumps(items, ensure_ascii=False, indent=1)),
                             FormRoles)
    except LLMError as e:
        an.warnings.append(f"form roles guessed from titles ({e})")
        return
    by_id = {it["id"]: k for it, (k, _, _) in zip(items, forms)}
    log = an.audit.setdefault("form_roles", {"roles": {}, "differs_from_title": []})
    for r in res.roles:
        k = by_id.get(r.id)
        if not k:
            continue
        for f in (an.fe.get(k), an.fr.get(k)):
            if f:
                guess = role_key(f, rub)
                f.role = r.role
        num = (an.fr.get(k) or an.fe[k]).number
        log["roles"][num] = r.role
        if guess and guess != r.role:
            log["differs_from_title"].append(f"{num}: text says {r.role}, title suggests {guess}")
    # the rows of forms on one policy only take the role-based default impact (rubric-locked rows stay as they are)
    for f in an.findings["forms"]:
        if f.kind not in ("form_removed", "form_added"):
            continue
        key = f.change_key.split(":", 1)[1]
        form = an.fe.get(key) if f.kind == "form_removed" else an.fr.get(key)
        if not form:
            continue
        f.context["form_role"] = form_role(form, rub)
        if not f.locked:
            r, locked = form_default_impact(form, "removed" if f.kind == "form_removed" else "added", rub)
            f.impact, f.severity, f.locked = r["impact"], r["severity"], locked
