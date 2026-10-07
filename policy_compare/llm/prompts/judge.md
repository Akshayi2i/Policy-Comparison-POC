You are checking a renewal comparison written by another assistant. Below are the FACTS established by the comparison
engine and the STATEMENTS written about them.

FACTS (JSON):
{facts}

STATEMENTS (JSON):
{statements}

Return in "unsupported" every statement that the facts do NOT support, with a short reason (max 20 words). A statement
is unsupported if it:
- says something changed, was removed, added, reduced or increased when the facts do not show that change;
- describes a notice form as an exclusion, or an unchanged form as changed;
- names an amount, date, form or coverage that is not in the facts;
- draws a conclusion that contradicts the facts (e.g. "cover is narrower" when nothing reduces cover).

Observations are about forms that are on BOTH policies (see "forms_on_both_policies" and "observation_quotes"):
they describe unchanged exclusions, not changes, so do not flag them for not being a change.

Do NOT flag statements just because they are vague, short, or a recommendation. Only flag factual problems.
Return an empty list if every statement is supported.
