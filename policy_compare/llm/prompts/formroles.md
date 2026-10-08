Classify what each insurance form does, from its text. "excerpt" is the start of the form; "key_sentence" is its
first operative sentence (may be empty).

Forms (JSON):
{forms}

Return "roles": one entry per form, with its "id" exactly as given and one "role":
- "coverage": grants cover, adds an insured or a coverage, or gives back cover that another form excludes (a buy-back,
  e.g. "the exclusion does not apply to ...")
- "exclusion": takes cover away or limits it (exclusions, limitations, restrictions, sub-limits)
- "notice": informs only and does not change cover by itself (important notices, privacy and disclosure notices)
- "condition": changes conditions, definitions or procedures, including state amendatory endorsements, cancellation
  and other-insurance clauses
- "schedule": declarations pages, forms schedules and lists of coverages or locations

Judge by what the text does, not only by the title: a form titled "... Coverage" that only restores part of an
exclusion is "coverage"; an "Important Notice" about an exclusion is "notice".
