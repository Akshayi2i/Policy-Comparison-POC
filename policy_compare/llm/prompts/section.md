Section {number} of the renewal comparison: **{title}**.

Policy context:
{context}

Compared items in this section (JSON). Items with "impact" changed between the two policies; items without it are unchanged and are listed for context only:
{items}

Tasks:
1. For every item that has an "impact", return an entry in "items" with its id, impact, severity and "why".
   - If "fixed_by_rules" is true, copy its impact and severity unchanged.
   - "why" is one or two sentences (max 45 words) on why the change matters to the insured. {why_hint}
2. "headline": one sentence (max 25 words) — the key takeaway for the section.
3. "bullets": up to 3 short key points (max 14 words each).
4. "takeaway": the same idea as the headline in max 20 words, for an overview table.
5. "risk_level": how much this section's changes expose the client (high / medium / low), with "risk_statement" (max 30 words).
6. "confidence" (high / medium / low) in the reading of these values and "confidence_reason" (max 30 words), based on how the values were found (page references, missing values, wording that needs a full read).

Do not mention amounts, dates or counts that are not in the items above.
