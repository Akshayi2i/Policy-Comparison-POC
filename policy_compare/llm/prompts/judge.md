You are checking a renewal comparison written by another assistant. Below are the FACTS established by the comparison
engine and the STATEMENTS written about them.

FACTS (JSON):
{facts}

STATEMENTS (JSON):
{statements}

Return in "unsupported" every statement that a fact CONTRADICTS. For each give "contradicted_by" (the id of that
fact, e.g. "F7") and a short reason (max 20 words). A statement is contradicted when it:
- says something changed, was removed, added, reduced or increased and a fact shows it did not;
- describes a notice form as an exclusion, or an unchanged form as changed;
- draws a conclusion a fact rules out (e.g. "cover is narrower" when the facts say nothing reduces cover).
A detail that the facts do not mention is NOT a contradiction (the form text says more than the facts list); leave it.

Observations are about forms that are on BOTH policies (see "forms_on_both_policies" and "observation_quotes"):
they describe unchanged exclusions, not changes, so do not flag them for not being a change.

Do NOT flag statements just because they are vague, short, or a recommendation. Only flag factual problems.
Return an empty list if every statement is supported.
