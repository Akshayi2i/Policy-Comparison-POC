Write the recommended actions for the account manager who will present this renewal. The policy expires on {deadline}.

Client profile:
{profile}

Findings of the comparison (JSON; each has an "id"). Only these findings may be used:
{evidence}

Return "actions": up to {max_items} actions, most important first. For each:
- "action": one imperative sentence (max 20 words) saying exactly what to do and with whom, e.g.
  "Ask the carrier why CP 382 was removed and whether New York still requires it."
- "reason": one or two sentences (max 30 words) on why it matters for THIS client's operations
- "priority": high (must be done before the expiry), medium (should be done), low (good practice)
- "audience": "carrier" (a question or request to the insurer), "client" (something to tell or ask the client) or
  "internal" (a check the broker does)
- "evidence_ids": 1-3 ids of the findings the action rests on

Rules:
- Every action must rest on the cited findings. Do not recommend anything the findings do not support.
- Be specific: name the form number or the item, and say what to ask or check. Avoid "review", "consider" or
  "monitor" without saying what exactly.
- Never recommend buying cover the client already has: read "amended_by" / "forms_that_amend_this_form" first.
- A finding with impact "no_impact" needs no action. An unchanged term ("observations") is something to discuss with
  the client, not a change.
- Combine findings that need the same action into one action instead of repeating it.
