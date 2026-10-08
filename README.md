# Policy Comparison Engine (POC)

Two policy JSONs in the `canonical_insurance_policy` schema go in. A renewal comparison PDF comes out, in the Fideon OS design of `Reference data/.../Comparison_Document_Colour.pdf`, with an optional grayscale print edition. Judgement and narrative come from **Qwen3-VL-Instruct**, served by vLLM on a RunPod pod behind its OpenAI-compatible proxy URL.

## Quick start

```bash
pip install -r requirements.txt
python -m playwright install chromium        # PDF renderer
cp .env.example .env                         # then fill in the RunPod URL / key / model

# compare two policies (input order does not matter — the earlier effective date is "expiring")
python -m policy_compare --a canonical_json/compare_policy_1.canonical.json \
    --b canonical_json/compare_policy_2.canonical.json \
    --focus "silica, additional insured"

python -m policy_compare ... --edition grayscale        # print edition
python -m policy_compare ... --no-llm                   # without the model
python -m policy_compare --from-report output/<name>.report.json --edition grayscale   # re-render only

# ask a question about the two policies (answer comes with verified quotes and page references)
python -m policy_compare --a p1.json --b p2.json --ask "Is snow removal covered?"

# policies with scanned pages: give the original PDFs so a vision model can read those pages
python -m policy_compare --a p1.json --b p2.json --pdf p1.pdf --pdf p2.pdf     # or --source-dir D:\PolicyPDFs
```

### Where reports are saved
Everything goes to the **`output/`** folder (`D:\POC4\output`; change it with `OUTPUT_DIR` in `.env`):

| File | Content |
|---|---|
| `output/<Insured>_renewal_comparison_<renewal date>.pdf` | the report (colour edition) |
| `output/<Insured>_renewal_comparison_<renewal date>_grayscale.pdf` | grayscale print edition |
| `output/<Insured>_renewal_comparison_<renewal date>.report.json` | full report model + audit trail (model calls, guard actions, rejected model text, warnings) |
| `output/<Insured>_renewal_comparison_<renewal date>.carrier_email.txt` | draft email to the carrier with the open questions |
| `output/<Insured>_renewal_comparison_<renewal date>.client_letter.txt` | draft plain-English renewal letter to the client |
| `output/.llm_cache/` | cached model answers |

`--out my_name` saves `output/my_name.pdf`; a path with folders (`--out D:\Reports\x.pdf`) is used as given. The API also saves every report in `output/` and returns its location in the `X-Saved-To` header.

### HTTP API
```bash
uvicorn policy_compare.api:app --port 8000
curl -F policy_a=@p1.json -F policy_b=@p2.json -F focus="silica" -F edition=colour \
     http://localhost:8000/compare -o report.pdf
curl http://localhost:8000/health
```
`POST /compare` also takes:
- `llm=false`, to run without the model.
- `format=json`, which returns the report model instead of the PDF. The drafts are under `drafts`.
- `pdf_a` / `pdf_b`, the original PDFs, used only to read scanned pages.

The response carries `X-Changes-Found`, `X-Critical-Changes`, `X-Overall-Risk` and `X-Model-Used` headers.

```bash
curl -F policy_a=@p1.json -F policy_b=@p2.json -F question="Is snow removal covered?" http://localhost:8000/ask
```
`POST /ask` returns `answer`, `citations` (each with `ref`, e.g. `R1 p.66`, and the verbatim `quote`), `verified` and the retrieved `passages`. An answer is returned only when at least one of its quotes is found word for word in the policy text. Otherwise `answer` says the text does not clearly answer the question, and the passages are returned for a person to read.

## RunPod / vLLM

Any OpenAI-compatible vLLM endpoint works. The model is chosen in `.env` only; no code changes.

**Qwen3-VL-8B-Instruct (target model; reads scanned pages too).** It needs a GPU with free memory: about 17 GB of weights plus KV cache, so at least 24 GB, with 40–48 GB recommended for a 32K context. Start it with a vLLM version that supports Qwen3-VL:
```bash
vllm serve /workspace/models/Qwen3-VL-8B-Instruct --served-model-name Qwen3-VL-8B-Instruct \
     --host 0.0.0.0 --port 8000 --max-model-len 32768 --api-key <secret>
```
```
LLM_BASE_URL=https://<pod-id>-8000.proxy.runpod.net/v1
LLM_API_KEY=<secret>
LLM_MODEL=Qwen3-VL-8B-Instruct        # the served model name; "VL" in the name switches scanned-page reading on
```

**Any text model already being served (e.g. Qwen2.5-7B-Instruct).** Set the same three values. Everything works except reading scanned pages.

| Setting | Default | Meaning |
|---|---|---|
| `LLM_MAX_CONTEXT` | 32768 | the served `--max-model-len`; output tokens are clamped so prompt + output fit |
| `LLM_MAX_OUTPUT_TOKENS` | 4096 | upper bound per answer |
| `LLM_VISION` | `auto` | `auto` = on when the model name contains "VL"; `true` / `false` to force |
| `OCR_MAX_PAGES` | 15 | most scanned pages read per policy |
| `SOURCE_PDF_DIR` | — | folder with the original policy PDFs, matched by file name or sha256 (same as `--source-dir`) |
| `LLM_JUDGE` | `true` | the second pass over the model's own statements; see the note below |

**Note on the second pass with a 7B model.** The second pass can only revert model text to the rule-based text, so it never makes a report wrong. With Qwen2.5-7B, though, it also flags some true statements: on the Halstead pair it reverted 10 of 21 statements, about half of them needlessly. That leaves more rule-based wording in the report. Use a stronger model, or set `LLM_JUDGE=false` to keep more of the model's wording. The other checks (verbatim quotes, numbers, contradictions with the forms schedule) still run either way.

How the client handles RunPod:
- **Streaming.** Responses are streamed because the RunPod proxy closes requests that stay silent for about 100 s.
- **Structured output.** It uses vLLM `response_format: json_schema` and falls back automatically to `guided_json`, then to prompt + validate if the server rejects it.
- **Validation.** Every answer is validated against a Pydantic schema, with one repair retry.
- **Cache.** Answers are cached on disk in `output/.llm_cache`.
- **Prompt size.** Prompts stay small, a few thousand tokens, because the model never receives the whole JSON. That JSON is about 70K tokens of page text; the model gets only the compared findings and short excerpts.

## How it works

```
JSON A, JSON B (+ optional source PDFs)
 → ingest        validate core fields; expiring = earlier effective date
 → scanned pages [model, vision] pages without a text layer are transcribed from the PDF
 → flatten       generic walk of every section; list items aligned by identity key (form_number, coverage, …),
                 never by position; classified into report sections by config/catalogue.yaml
 → diff          values, deltas, %, continuity gap, coverage parts, admitted status (code, rubric-driven)
 → forms         schedule by normalized form number; edition changes; verbatim wording diff per form
 → equivalence   [model] removed + added forms that are one replacement (equivalent / narrower / broader)
 → model         per-section impact / severity / "why it matters" for this client, summaries, risk & confidence
 → contracts     [model] §9 requirements read from the form wording
 → cross-ref     §8 mid-term changes, §9 contract checklist, §10 client focus, §11 pending items
 → observations  [model] §12 unchanged exclusions that matter for this client
 → model         executive verdict, top-10 critical changes with themes and next steps, focus quotes
 → second pass   [model] re-reads its own statements against the facts; unsupported ones are reverted
 → guards        schema checks, numbers must exist in the facts, quotes must be verbatim, risk floor/ceiling
 → drafts        [model] carrier email + client letter (rule-based drafts without the model)
 → build         ReportModel (all counts, KPIs, chips computed by code)
 → render        Jinja2 + CSS → Chromium → US Letter PDF (header/footer stamped with Inter)
```

### Where the model is used

Every model claim is checked by code before it is used. If a check fails, the claim is dropped and the reason is logged in the audit (`rejected_text`, `form_equivalence`, `form_roles`, `recommendations`, `judge`).

| Feature | What the model does | What code checks | Where it shows |
|---|---|---|---|
| Form roles | reads each form and says what it does: coverage (incl. buy-backs), exclusion, notice, condition or schedule | — (one batched call; without the model the role is guessed from title words) | default impact of removed / added forms (a removed buy-back is a likely reduction, a removed notice is not) |
| Section assessment | impact and severity of unlocked changes, judged against the client's business (class, state, coverage parts); "why it matters" | numbers must be in the facts; no claims that contradict the forms schedule; risk floor and ceiling | §3–§7 |
| Form equivalence | pairs a removed form with an added form that replaces it, quoting both | both quotes must be verbatim in each form's own pages; each form is used once; a lost contract requirement stays a locked reduction | §7 "Forms replaced", FORMS KPI |
| Contract requirements | reads the AI / P&NC / waiver wording in the forms | the quote must be verbatim in the named form | §9 rows, with a "From the form wording" quote |
| Focus topics | picks topics for this industry when `--focus` is not given | topics must appear in the policy text | §10 |
| Coverage observations | unchanged exclusions that matter for this client, with a recommendation; it also sees the endorsements that amend each exclusion (e.g. a buy-back) | the form must be on both policies; the quote must be verbatim in that form; the row always names the amending forms | §12 (not counted as changes) |
| Recommended actions | up to 6 specific actions (who, what, why) for the account manager | each action must cite findings by id; priority is capped by what those findings support; every critical change keeps an action | Executive Summary "Recommended actions"; the carrier email's questions |
| Second pass | lists its own statements that a numbered fact contradicts | the fact it cites must exist, otherwise nothing is reverted; it only reverts to rule-based text | all model text |
| Drafts | carrier email and client letter | the same gate as the report text | `.carrier_email.txt`, `.client_letter.txt`, `drafts` in JSON |
| Questions | answers from retrieved passages | the citations must be verbatim; the answer is withheld otherwise | `--ask`, `POST /ask` |
| Scanned pages | transcribes page images (vision model only) | — (the text is used like any other page text; the pages are listed in Appendix A) | Appendix A "How it was read" |

**Forms that name other forms.** When a form's own text names another form (by number, or by its full title), the
two are linked, e.g. SROCNY ("Snow Removal Operations Coverage") names SNEXNY ("Exclusion Snow Removal Operations")
and gives part of that cover back. The model sees linked forms together, and the report shows the link.

**Checks on all model text.** Numbers must exist in the facts; a statement may not contradict the forms schedule
(by title or by form number); no "reduced cover" claim where nothing was reduced; no "changes" or "affected" claim
where nothing material changed; no talk about the comparison itself ("values from the report", "quotes match"); no
mention of form editions when none changed. Title Case is put into sentence case rather than thrown away.

**Layout.** Sections where nothing changed show one summary line instead of the two cards. If the reference page breaks
would leave a page nearly empty (e.g. one table row), the report is printed again with the appendices kept together.

**What code does and what the model does.** Code owns every value, amount, percentage, date, page reference, count and KPI. The model only classifies, explains, ranks and writes. It refers to findings by id and never returns values.

**What happens when the model is not available.** If no endpoint is configured, a call fails, or an answer fails a guard, the report uses the rule-based default for that one field. So a report is always produced.

**Counting.**
- "N changes found" counts **unique changes**. Each change has one `change_key`; rows in §8–§11 that repeat an earlier change are not counted again.
- Each section's "items compared" counts its own classified rows. Unchanged rows are shown but not counted.

## Configuration (no policy-specific code)

| File | Purpose |
|---|---|
| `config/catalogue.yaml` | path/label rules → report section, group, label, type; identity keys for list alignment; metadata keys to ignore; expected elements for "Not shown on either declarations page"; coverage parts; text signals |
| `config/rubric.yaml` | deterministic impact/severity rules and thresholds, default explanations, next steps, themes, risk floors |
| `config/contract_slots.yaml` | §9 contract requirements (ISO form hints + title keywords) and broker checks |
| `config/branding.yaml` | agency, brand, "prepared by", footer, disclaimer |

Fields that match no catalogue rule are classified by the model in one batched call. Without the model they default to *Coverage Terms*. They are listed in the audit (`unmatched_paths`) so a rule can be added.

## Tests and tools

```bash
python -m pytest                       # units, fixture, Halstead truth, scenarios, model plumbing (mock server),
                                       # AI features with a stub model (tests/test_ai_features.py)
python tools/make_scenarios.py         # write the synthetic scenario pairs to tests/scenarios/
python tools/mock_llm_server.py        # OpenAI-compatible mock for running without a pod
python tools/render_fixture.py tests/fixtures/summit_ridge.report.json output/fixture.pdf   # reference transcription
python tools/visual_diff.py ours.pdf reference.pdf output/diff --pages 1,2               # side-by-side PNGs
python tools/layout_diff.py ours.pdf reference.pdf --page 4                              # line-position deltas
```

`tests/fixtures/summit_ridge.report.json` is a transcription of the reference PDF. Rendering it reproduces the reference page for page: 12 pages, the same page breaks, and line positions mostly within ±2 pt.

## Notes and limits of the POC

- **Mid-term changes (§8).** Schema v1.0 has no endorsement or policy-change structure. Change documents are detected by title, and their values are read by the model; quotes must be verbatim. Without the model they are listed as "review".
- **Page references.** Values carry no page numbers in the schema, so pages are located by searching the page text. A value that cannot be located has no chip.
- **Focus areas (§10).** Mentions and candidate quotes are extracted by code; the model only chooses among them.
