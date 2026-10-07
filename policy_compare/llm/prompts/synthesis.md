You are writing the executive layer of a renewal comparison for {insured}.

Policies:
{context}

Section results (headline and risk per section):
{sections}

All distinct changes (JSON):
{changes}

Candidate critical changes — reduced cover at critical or high severity (JSON):
{candidates}

Contract requirements and broker checklist rows (JSON):
{checklist}

Client focus topics with related changes and candidate quotes (JSON; quotes are numbered per side):
{focus}

Items pending confirmation with the carrier (JSON):
{pending}

The "Policies" block above also lists every form change ("form_changes", with each form's role and any related form
still on the renewal) and the forms on both policies ("forms_on_both_policies"). Never describe a form on both policies
as removed or added, and never call a removed notice a removed exclusion.

Tasks:
1. "risk": overall risk for presenting this renewal (high / medium / low) and "confidence" in the comparison.
2. "lead": one short imperative verdict sentence for the account manager (max 12 words), e.g. "Do not present this renewal as like-for-like." or "Like-for-like renewal; confirm one form."
3. "body": 1–2 sentences (max 55 words) naming the main drivers in plain words. Do NOT state how many critical changes there are (the report adds that), and do not invent numbers.
4. "confidence_note": one sentence (max 30 words) starting "Confidence is ..." explaining why.
5. "critical": choose and rank up to {max_items} items ONLY from the candidate ids. Include every candidate with severity "critical". Rank by severity, then group by theme in this order: continuity, scope, limits, contracts, market. For each: "title" (max 6 words), "theme", "description" (max 12 words, the consequence for the client) and "next_step" (max 12 words, a concrete action before the expiry date).
6. "checklist", "focus", "pending": section texts in the same style as the other sections — headline one complete sentence (max 25 words), up to 3 bullets that are short complete sentences, takeaway one sentence (max 20 words), risk level + one-sentence statement, confidence + one-sentence reason. Sentence case, never Title Case labels.
7. "focus_quotes": for each focus topic, the indexes of the 1–2 most relevant expiring quotes and renewal quotes (use [] when none fits).
