Construction and service contracts commonly require the insured's liability policy to provide the items below. Decide,
from the form passages given, whether THIS policy meets each requirement.

Client profile:
{profile}

Requirements, each with the forms on this policy whose text mentions it (JSON; "passages" are verbatim excerpts):
{slots}

For every requirement return one verdict:
- "slot": the slot id exactly as given
- "status": "met" if a form clearly provides it; "partial" if it is provided only in part (e.g. blanket additional
  insured that covers ongoing but not completed operations, or only when a written contract requires it);
  "not_found" if no passage provides it
- "form_number": the form that provides it (exactly as given), or null for not_found
- "quote": a short verbatim excerpt (max 30 words) copied exactly from that form's passages that proves the verdict,
  or null for not_found
- "note": one sentence (max 25 words) explaining the verdict in plain words

Only use the passages given. If the passages do not show the requirement, answer "not_found".
