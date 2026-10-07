"""Model-text gate: contradictions with the forms schedule, sentence style, logged rejections."""
import pytest

from tests.conftest import POLICY_1, POLICY_2
from policy_compare.analysis.guards import ClaimChecker, TextGate, check_text
from policy_compare.diff import expected_status
from policy_compare.engine import analyse


@pytest.fixture(scope="module")
def an():
    return analyse(POLICY_1, POLICY_2, use_llm=False)


@pytest.mark.parametrize("text", [
    "Communicable disease exclusion removed.",
    "Anti-arson and communicable disease exclusions removed.",
    "The silica exclusion was added at renewal.",
])
def test_contradictions_are_caught(an, text):
    assert ClaimChecker(an).problem(text)


@pytest.mark.parametrize("text", [
    "The communicable disease notice was removed; the exclusion itself remains.",
    "The NY anti-arson amendment was removed at renewal.",
    "Two forms were removed at renewal.",
])
def test_true_statements_pass(an, text):
    assert ClaimChecker(an).problem(text) is None


def test_sentence_style():
    assert check_text("NY Anti Arson Amendment", 20, kind="headline")[0] is None
    assert check_text("Pending Confirmation", 20, kind="bullet")[0] is None
    assert check_text("Dates moved forward one year", 20, kind="bullet")[0] == "Dates moved forward one year."


def test_gate_logs_rejections(an):
    gate = TextGate(an)
    assert gate.take("test field", "Communicable disease exclusion removed.", 20, kind="bullet") is None
    log = an.audit["rejected_text"][-1]
    assert log["field"] == "test field" and log["reason"].startswith("contradicts the facts")


def test_removed_notice_carries_related_form(an):
    dncany = next(f for f in an.findings["forms"] if f.context.get("form_number") == "DNCANY")
    assert dncany.context["form_role"].startswith("notice")
    assert dncany.context["related_forms_still_on_renewal"] == ["AP 0853UF — Exclusion - Communicable Disease"]


def test_expected_item_provided_by_form(an):
    missing, via_form = expected_status("limits", an)
    assert "Business Income / Extra Expense" not in missing
    assert via_form and "AP 0611" in via_form[0] and "both policies" in via_form[0]


def test_form_numbers_are_allowed_numbers(an):
    assert TextGate(an).take("t", "The notice was removed; cover continues under AP 0853UF.", 20) is not None


def test_proper_nouns_are_not_title_case(an):
    gate = TextGate(an)
    assert gate.take("t", "Confirm CP 382 NY Anti Arson Amendment.", 16, kind="headline") is not None
    assert gate.take("t", "Pending Carrier Confirmation Required", 16, kind="headline") is None


def test_reduction_claims_need_a_reduction(an):
    gate = TextGate(an)
    assert gate.take("t", "Narrowed coverage for communicable diseases.", 20, no_reduction=True) is None
    assert gate.take("t", "Nothing in this section reduces cover.", 20, no_reduction=True) is not None
    assert gate.take("t", "Narrowed coverage for communicable diseases.", 20, no_reduction=False) is not None


def test_internal_jargon_is_rejected(an):
    assert TextGate(an).take("t", "Form fixed by rules.", 20) is None


def test_form_title_words_are_not_removal_claims(an):
    # "snow removal" names the form; it does not say a form was removed
    assert ClaimChecker(an).problem("Snow removal operations excluded.") is None
    assert ClaimChecker(an).problem("Snow removal operations coverage was removed at renewal.")


def test_truncated_and_short_quotes(an):
    from policy_compare.textindex import MIN_QUOTE_WORDS
    full = "We do not pay for bodily injury, property damage, personal injury, or advertising injury that arises out of"
    assert an.ti_r.contains_verbatim(full + "...", MIN_QUOTE_WORDS) == 28          # cut short with an ellipsis
    assert an.ti_r.contains_verbatim("“…" + full[10:] + "”", MIN_QUOTE_WORDS) == 28
    assert an.ti_r.contains_verbatim("bodily injury", MIN_QUOTE_WORDS) is None       # too short to verify anything


def test_edition_claims_need_an_edition_change(an):
    assert ClaimChecker(an).problem("NY Anti-Arson Amendment removed; confirm form edition.")
    assert ClaimChecker(an).problem("No form editions changed.") is None
