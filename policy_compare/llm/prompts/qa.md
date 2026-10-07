Answer the broker's question about two versions of a client's insurance policy (EXPIRING and RENEWAL), using ONLY
the passages below. They were retrieved from the policy text; page numbers are given.

Question: {question}

Passages (JSON, by policy):
{passages}

Return:
- "answer": 1–3 plain sentences (max 80 words). Say whether the answer differs between the expiring and renewal
  policy. If the passages do not answer the question, say so — do not guess.
- "citations": 1–4 short verbatim quotes (max 30 words each) copied exactly from the passages, each with its "side"
  ("expiring" or "renewal").
- "confidence": high / medium / low.
