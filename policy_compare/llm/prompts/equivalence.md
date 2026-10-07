At renewal some forms were REMOVED and others were ADDED (different form numbers). A carrier often replaces its own
form with another form that does the same job. Decide which added form, if any, replaces each removed form.

Removed forms (on the expiring policy only; "excerpt" is the start of the form's text):
{removed}

Added forms (on the renewal policy only):
{added}

Return "pairs": one entry per removed form that has a replacement among the added forms. Each form may appear in at
most one pair. Only pair forms that serve the same purpose (same coverage, exclusion, condition or notice); forms
that merely share words are not a pair. Leave a removed form out when no added form replaces it.

For each pair:
- "removed_id" and "added_id": the ids exactly as given ("X1", "N2", ...)
- "relation": "equivalent" (same effect), "narrower" (the renewal form gives less cover or restricts more) or
  "broader" (the renewal form gives more cover or restricts less)
- "reason": one or two sentences (max 35 words) on what the forms do and how the renewal wording differs
- "expiring_quote": a short verbatim passage (5-25 words) copied exactly from the removed form's excerpt
- "renewal_quote": a short verbatim passage (5-25 words) copied exactly from the added form's excerpt, showing the
  same provision

Return an empty list when no added form replaces a removed form.
