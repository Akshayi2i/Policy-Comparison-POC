"""AI features A-J with a stub model: every model claim is checked against the documents before it is used."""
import copy
import json
import re

import pytest

from tests.conftest import POLICY_1, POLICY_2
from make_scenarios import _add_form, load_base
from policy_compare.engine import analyse
from policy_compare.llm.client import LLMError
from policy_compare.report.build import build_report
from policy_compare.textindex import form_key, norm_space

SECTION = {"headline": "Two exclusions matter for this contractor.", "bullets": ["Subcontractor injuries are excluded."],
           "takeaway": "Discuss the unchanged exclusions with the client.", "risk_level": "medium",
           "risk_statement": "Existing exclusions leave gaps for this kind of work.", "confidence": "high",
           "confidence_reason": "Quotes were read from the form text."}


class StubLLM:
    """Answers by task name (exact or prefix); a callable answer gets the prompt. Unknown tasks fail like a dead pod."""

    def __init__(self, answers: dict | None = None, vision: str | None = None):
        self.answers, self.vision, self.calls = answers or {}, vision, []

    def structured(self, task, user, out, **kw):
        a = self.answers.get(task)
        if a is None:
            a = next((v for k, v in self.answers.items() if task.startswith(k)), None)
        if a is None:
            self.calls.append({"task": task, "ok": False})
            raise LLMError(f"{task}: no stub answer")
        self.calls.append({"task": task, "ok": True})
        return out.model_validate(a(user) if callable(a) else a)

    def vision_text(self, task, instruction, png):
        self.calls.append({"task": task, "ok": True})
        if self.vision is None:
            raise LLMError("no vision")
        return self.vision


@pytest.fixture()
def an():
    return analyse(POLICY_1, POLICY_2, use_llm=False)


@pytest.fixture()
def with_stub(monkeypatch):
    """Route the engine's LLMClient() to a stub and switch the model on."""
    from policy_compare.settings import settings

    def install(stub: StubLLM, model: str = "stub-qwen2.5"):
        monkeypatch.setattr("policy_compare.llm.client.LLMClient", lambda: stub)
        monkeypatch.setattr(settings(), "llm_base_url", "http://stub/v1")
        monkeypatch.setattr(settings(), "llm_model", model)
        return stub
    return install


def _form_quote(an, number: str, start: int = 20, n: int = 14) -> tuple[str, int]:
    """A verbatim run of words from one page of a renewal form, and that page."""
    f = an.fr[form_key(number)]
    page = f.doc.pages[0].page_number
    words = norm_space(an.ti_r.pages[page]).split()
    return " ".join(words[start:start + n]), page


# ---------- A: coverage observations ----------
def test_observations_keep_only_verified_quotes(an):
    from policy_compare.analysis.observe import observe
    from policy_compare.diff import Ids

    quote, page = _form_quote(an, "XCNTR")
    obs = lambda num, q: {"form_number": num, "concern": "Injury to contractors' employees is excluded.",
                          "why": "The insured hires subcontractors, so injuries to their workers fall outside this policy.",
                          "recommendation": "Collect subcontractor certificates before work starts.", "severity": "high", "quote": q}
    stub = StubLLM({"observations": {**SECTION, "observations": [
        obs("XCNTR", quote),
        obs("AP 0853UF", "communicable disease is never covered under any circumstances whatsoever"),   # not verbatim
        obs("ZZZ 1", quote),                                                                             # unknown form
        obs("CP 382", quote),                                                                            # not on both policies
    ]}})
    before = len(an.unique_changes())
    rows = observe(stub, an, Ids())
    assert len(rows) == 1
    r = rows[0]
    assert r.section == "observations" and r.sublabel.startswith("XCNTR")
    assert f"[[R1 p.{page}]]" in r.why and quote in r.why
    reasons = [x["reason"] for x in an.audit["rejected_text"]]
    assert sum("not verbatim" in x for x in reasons) == 1 and sum("not an unchanged restrictive form" in x for x in reasons) == 2
    # 3 of 4 observations were dropped, so the model's own summary is replaced by one built from the kept rows
    assert an.narratives.sections["observations"].headline ==         "Unchanged terms to discuss with the client: injury to contractors' employees is excluded."
    an.findings["observations"] = rows
    assert len(an.unique_changes()) == before             # observations are not changes
    rep = build_report(an)
    sec = next(s for s in rep.sections if s.number == "12")
    assert sec.blocks and "Injury to contractors" in json.dumps(sec.model_dump(), ensure_ascii=False)


def test_failed_observation_call_is_not_reported_as_none_found(with_stub):
    with_stub(StubLLM())                                    # every call fails
    an = analyse(POLICY_1, POLICY_2, use_llm=True)
    assert not an.audit.get("observations_ran")
    assert "need the model" in an.narratives.sections["observations"].headline
    assert any("coverage observations failed" in w for w in an.warnings)


# ---------- B: cross-carrier form equivalence ----------
def _replacement_pair():
    e, r = load_base()
    cp382 = next(d for d in e["documents"] if d.get("form_number") == "CP 382")
    text = "\n".join(p["text"] for p in cp382["pages"])
    _add_form(r, "UFNY 382", "01 26", "New York Amendatory Endorsement - Anti-Arson", text)
    return e, r


def test_equivalent_form_becomes_one_replacement_row(with_stub):
    e, r = _replacement_pair()
    before = len(analyse(e, r, use_llm=False).unique_changes())
    q = "The insured will be furnished an anti-arson application if applicable."

    def answer(user):
        xid = {num: i for i, num in re.findall(r'"id": "(X\d+)",\s*"form_number": "([^"]+)"', user)}
        nid = re.search(r'"id": "(N\d+)",\s*"form_number": "UFNY 382"', user).group(1)
        return {"pairs": [
            {"removed_id": xid["DNCANY"], "added_id": nid, "relation": "equivalent", "reason": "Same notice.",
             "expiring_quote": "this sentence is not in the notice at all", "renewal_quote": q},
            {"removed_id": xid["CP 382"], "added_id": nid, "relation": "equivalent",
             "reason": "Both forms set the New York anti-arson application rules; the wording is the same.",
             "expiring_quote": q, "renewal_quote": q},
        ]}

    with_stub(StubLLM({"form_equivalence": answer}))      # every other model step fails and falls back
    an = analyse(e, r, use_llm=True)
    forms = an.findings["forms"]
    rep_rows = [f for f in forms if f.kind == "form_replaced"]
    assert len(rep_rows) == 1
    f = rep_rows[0]
    assert f.context["form_number"] == "CP 382" and f.sublabel.startswith("Replaced by UFNY 382")
    assert f.impact == "no_impact" and f.ren.startswith("UFNY 382")
    assert "[[E1 p." in f.why and "[[R1 p." in f.why
    assert not [x for x in forms if x.kind == "form_added"]
    assert [x.context["form_number"] for x in forms if x.kind == "form_removed"] == ["DNCANY"]
    assert len(an.unique_changes()) == before - 1        # removed + added counted once
    log = an.audit["form_equivalence"]
    assert [x["kept"] for x in log] == [False, True] and "not verbatim" in log[0]["reason"]
    rep = build_report(an)
    row = next(r for s in rep.sections if s.number == "7" for bl in s.blocks for r in getattr(bl, "rows", []) or []
               if r.label.startswith("CP 382"))
    assert row.explain and q in row.explain                # evidence shown even though there is no impact
    kpi = rep.executive.kpis[2]
    assert kpi.value == "+0 / −1" and kpi.sub.endswith("1 replaced")


def test_no_pairing_without_both_removed_and_added(an):
    from policy_compare.analysis.equivalence import match_equivalents
    stub = StubLLM()
    match_equivalents(stub, an)                           # Halstead: forms removed, none added
    assert stub.calls == []


# ---------- C: contract requirements from the form wording ----------
def test_slot_evidence_needs_a_verbatim_quote(an):
    from policy_compare.analysis.slots import assess_slots, candidates
    from policy_compare.settings import config
    from policy_compare.xref import checklist

    slot = next(s for s in config("contract_slots")["slots"] if s["id"] == "waiver_subrogation")
    cand = candidates(an.fr, an.ti_r)["waiver_subrogation"][0]
    form = an.fr[form_key(cand["form_number"])]
    page, quote = None, None
    for p in form.doc.pages:
        text = norm_space(an.ti_r.pages[p.page_number])
        hit = next((w for w in slot["text_any"] if w.lower() in text.lower()), None)
        if hit:
            i = text.lower().index(hit.lower())
            quote, page = text[i: i + 80].rsplit(" ", 1)[0], p.page_number
            break
    assert quote
    stub = StubLLM({"slots_": {"verdicts": [
        {"slot": "waiver_subrogation", "status": "met", "form_number": form.number, "quote": quote, "note": "Waiver granted."},
        {"slot": "per_project_aggregate", "status": "met", "form_number": form.number,
         "quote": "aggregate limits apply separately to each of your projects", "note": "Invented."},
    ]}})
    ev = assess_slots(stub, an)
    assert [c["task"] for c in stub.calls] == ["slots_expiring"]          # same candidates on both sides: one call
    assert ev["renewal"]["waiver_subrogation"].page == page
    assert "per_project_aggregate" not in ev["renewal"]
    assert any(x["field"].endswith("per_project_aggregate") for x in an.audit["rejected_text"])
    assert "waiver_subrogation" in form.slots
    base = [f for s in ("policy", "premium", "limits", "terms", "forms", "midterm") for f in an.findings[s]]
    from policy_compare.diff import Ids
    rows = checklist(an.E, an.R, an.fe, an.fr, base, [], an.ti_e, an.ti_r, Ids(), evidence=ev)
    w = next(f for f in rows if f.context.get("slot") == "waiver_subrogation")
    assert w.ren == f"Yes - {form.number}" and "From the form wording" in w.sublabel and not w.changed


# ---------- D: drafts ----------
def test_default_drafts_and_files(an, tmp_path):
    from policy_compare.analysis.drafts import write_drafts
    d = an.drafts
    assert d.source == "default"
    assert "ART3000742185" in d.carrier_email.subject and an.deadline in d.carrier_email.subject
    assert d.client_letter.body.startswith("Dear DEREK P HALSTEAD CONSTRUCTION CO INC")
    paths = write_drafts(d, tmp_path / "report")
    assert [p.name for p in paths] == ["report.carrier_email.txt", "report.client_letter.txt"]
    assert paths[0].read_text(encoding="utf-8").startswith("Subject: ")


def test_model_drafts_pass_the_gate(an):
    from policy_compare.analysis.drafts import build_drafts
    stub = StubLLM({"drafts": {
        "carrier_email": {"subject": "Renewal questions for Derek P Halstead Construction",
                          "body": "Hello,\n\nPlease confirm why CP 382 was removed at renewal.\n\nThank you."},
        "client_letter": {"subject": "Your renewal",
                          "body": "Dear client,\n\nThe communicable disease exclusion was removed, so you are now covered.\n\nRegards"},
    }})
    d = build_drafts(an, stub)
    assert d.source == "model"
    assert "CP 382 was removed" in d.carrier_email.body and "\n\n" in d.carrier_email.body   # line breaks kept
    assert d.client_letter.body == an.drafts.client_letter.body                                # false claim -> default
    assert any(x["field"] == "client letter body" for x in an.audit["rejected_text"])


# ---------- E: questions ----------
def test_answer_needs_verbatim_citations(with_stub):
    from policy_compare.qa import ask
    q = ("We do not pay for bodily injury, property damage, personal injury, or advertising injury that arises out of "
         "the actual or alleged transmission of a communicable disease")
    with_stub(StubLLM({"qa": {"answer": "Yes. Both policies exclude injury from the transmission of a communicable disease.",
                              "citations": [{"side": "renewal", "quote": q}], "confidence": "high"}}))
    res = ask(POLICY_1, POLICY_2, "Is communicable disease excluded?")
    assert res["verified"] and res["citations"][0]["ref"] == "R1 p.28"

    with_stub(StubLLM({"qa": {"answer": "No, it is fully covered.", "citations": [{"side": "renewal", "quote": "fully covered"}],
                              "confidence": "high"}}))
    res = ask(POLICY_1, POLICY_2, "Is communicable disease excluded?")
    assert not res["verified"] and "does not clearly answer" in res["answer"] and res["passages"]


def test_retrieval_only_without_model():
    from policy_compare.qa import ask
    res = ask(POLICY_1, POLICY_2, "snow removal", use_llm=False)
    assert res["answer"] is None and any("snow" in p["text"].lower() for p in res["passages"])


# ---------- F: scanned pages ----------
def _scanned_renewal(tmp_path, page_no: int = 76):
    import pymupdf
    e, r = load_base()
    r = copy.deepcopy(r)
    for d in r["documents"]:
        for p in d["pages"]:
            if p["page_number"] == page_no:
                p["text"] = ""
    pdf = tmp_path / r["source_document"]["file_name"]
    doc = pymupdf.open()
    for _ in range(page_no):
        doc.new_page()
    doc.save(pdf)
    return e, r, pdf


def test_scanned_page_is_read_by_the_vision_model(tmp_path):
    from policy_compare.ingest import load_pair
    from policy_compare.ocr import run_ocr
    from policy_compare.textindex import TextIndex

    e, r, pdf = _scanned_renewal(tmp_path)
    E, R, _ = load_pair(e, r)
    stub = StubLLM(vision="EXCLUSION OF INJURY TO EMPLOYEES, CONTRACTORS AND EMPLOYEES OF CONTRACTORS. Read from the image.")
    warnings: list[str] = []
    assert run_ocr(stub, [E, R], [pdf], warnings) == {"R1": [76]}
    assert R.ocr_pages == [76] and "Read from the image" in TextIndex(R).pages[76]
    assert not warnings

    E, R, _ = load_pair(e, r)
    warnings = []
    assert run_ocr(stub, [E, R], [], warnings) == {}
    assert "source PDF was not provided" in warnings[0]


def test_engine_reads_scanned_pages_with_a_vl_model(tmp_path, with_stub):
    e, r, pdf = _scanned_renewal(tmp_path)
    with_stub(StubLLM(vision="EXCLUSION OF INJURY TO EMPLOYEES. Transcribed page text for the test."), model="Qwen3-VL-8B-Instruct")
    an = analyse(e, r, use_llm=True, pdfs=[pdf])
    assert an.audit["vision"] and an.audit["ocr_pages"] == {"R1": [76]}
    src = {d.ref: d for d in build_report(an).sources}
    assert "scanned page read by the vision model" in src["R1"].how_read


# ---------- G: second-pass check ----------
def test_judge_reverts_unsupported_statements(an):
    from policy_compare.analysis.judge import judge
    n = an.narratives.sections["forms"]
    default_headline = n.headline
    n.headline, n.source = "The silica exclusion was added at renewal.", "model"

    def answer(user):
        stmts = json.loads(user.split("STATEMENTS (JSON):", 1)[1].split("\n\nReturn", 1)[0])
        return {"unsupported": [{"id": s["id"], "reason": "No form was added."} for s in stmts if "silica" in s["text"]]}

    judge(StubLLM({"judge": answer}), an)
    assert n.headline == default_headline
    assert an.audit["judge"][0]["field"].endswith("headline") and an.audit["judge_checked"] >= 1


# ---------- H / I: client profile drives severity and topics ----------
def test_client_profile_is_in_the_model_context(an):
    from policy_compare.analysis.assess import client_profile, policy_context
    p = client_profile(an)
    assert p["insured"] == "DEREK P HALSTEAD CONSTRUCTION CO INC" and p["policy_type"]
    assert "client_profile" in policy_context(an)


# ---------- J: vision follows the model ----------
@pytest.mark.parametrize("model,vision,expected", [
    ("Qwen/Qwen3-VL-8B-Instruct", "auto", True), ("qwen2.5-7b-instruct", "auto", False),
    ("Qwen/Qwen3-VL-8B-Instruct", "false", False), ("qwen2.5-7b-instruct", "true", True),
])
def test_vision_switch(model, vision, expected):
    from policy_compare.settings import Settings
    s = Settings(llm_base_url="http://x/v1", llm_model=model, llm_vision=vision)
    assert s.vision_enabled is expected


# ---------- everything at once: a dead model never breaks the report ----------
def test_all_ai_steps_fail_gracefully(with_stub):
    stub = with_stub(StubLLM())
    an = analyse(POLICY_1, POLICY_2, focus=["communicable disease"], use_llm=True)
    rep = build_report(an)
    assert rep.executive.changes_total == len(an.unique_changes())
    assert an.drafts.source == "default"
    tasks = {c["task"] for c in stub.calls}
    assert {"section_forms", "observations", "drafts"} <= tasks


def test_judge_skips_rule_based_text_in_model_sections(an):
    from policy_compare.analysis.judge import collect
    n = an.narratives.sections["focus"]
    n.source = "model"                                   # model section whose texts all fell back to the defaults
    assert not [s for s in collect(an) if s.field.startswith("Client Focus Areas")]
    n.takeaway = "Review the communicable disease exclusion with the client."
    assert [s.field for s in collect(an) if s.field.startswith("Client Focus Areas")] == ["Client Focus Areas takeaway"]


def test_reverted_risk_statement_takes_its_level(an):
    from policy_compare.analysis.judge import judge
    n = an.narratives.sections["forms"]
    default_level = n.risk_level
    n.risk_level, n.risk_statement, n.source = "high", "Removing these forms leaves major gaps.", "model"

    def answer(user):
        stmts = json.loads(user.split("STATEMENTS (JSON):", 1)[1].split("\n\nReturn", 1)[0])
        return {"unsupported": [{"id": s["id"], "reason": "No gap."} for s in stmts if "major gaps" in s["text"]]}

    judge(StubLLM({"judge": answer}), an)
    assert n.risk_level == default_level and n.risk_statement != "Removing these forms leaves major gaps."


def test_judge_cannot_delete_verified_observations(an):
    from policy_compare.analysis.judge import judge
    from policy_compare.analysis.observe import observe
    from policy_compare.diff import Ids

    quote, page = _form_quote(an, "XCNTR")
    stub = StubLLM({"observations": {**SECTION, "observations": [
        {"form_number": "XCNTR", "concern": "Injury to contractors' employees is excluded.",
         "why": "Most of this client's site work is done by subcontractors.", "recommendation": "Collect certificates.",
         "severity": "high", "quote": quote}]}})
    an.findings["observations"] = observe(stub, an, Ids())

    def answer(user):
        stmts = json.loads(user.split("STATEMENTS (JSON):", 1)[1].split("\n\nReturn", 1)[0])
        return {"unsupported": [{"id": s["id"], "reason": "x"} for s in stmts if "subcontractors" in s["text"]]}

    judge(StubLLM({"judge": answer}), an)
    rows = an.findings["observations"]
    assert len(rows) == 1                                  # the verified row stays
    assert rows[0].why == f"“{quote}” [[R1 p.{page}]]"     # only the unsupported explanation is withdrawn


def test_withdrawn_explanation_is_removed_from_copies(an):
    from policy_compare.analysis.judge import _withdraw_why
    from policy_compare.xref import pending
    from policy_compare.diff import Ids

    cp = next(f for f in an.findings["forms"] if f.context.get("form_number") == "CP 382")
    cp.why_default, cp.why, cp.why_source = cp.why, "Removed; an application is due within 45 days.", "model"
    an.findings["pending"] = pending([cp], Ids())
    assert an.findings["pending"][0].sublabel == cp.why
    _withdraw_why(an, cp)
    assert cp.why_source == "template" and an.findings["pending"][0].sublabel is None
