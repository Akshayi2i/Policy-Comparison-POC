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
```

### Where reports are saved
Everything goes to the **`output/`** folder (`D:\POC4\output`; change it with `OUTPUT_DIR` in `.env`):

| File | Content |
|---|---|
| `output/<Insured>_renewal_comparison_<renewal date>.pdf` | the report (colour edition) |
| `output/<Insured>_renewal_comparison_<renewal date>_grayscale.pdf` | grayscale print edition |
| `output/<Insured>_renewal_comparison_<renewal date>.report.json` | full report model + audit trail (model calls, guard actions, warnings) |
| `output/.llm_cache/` | cached model answers |

`--out my_name` saves `output/my_name.pdf`; a path with folders (`--out D:\Reports\x.pdf`) is used as given. The API also saves every report in `output/` and returns its location in the `X-Saved-To` header.

### HTTP API
```bash
uvicorn policy_compare.api:app --port 8000
curl -F policy_a=@p1.json -F policy_b=@p2.json -F focus="silica" -F edition=colour \
     http://localhost:8000/compare -o report.pdf
curl http://localhost:8000/health
```
`POST /compare` also takes `llm=false` and `format=json`, which returns the report model instead of the PDF. The response carries `X-Changes-Found`, `X-Critical-Changes`, `X-Overall-Risk` and `X-Model-Used` headers.

## RunPod / vLLM

Start Qwen3-VL-Instruct with vLLM on the pod (a vLLM version with Qwen3-VL support) and expose port 8000:
```bash
vllm serve Qwen/Qwen3-VL-8B-Instruct --host 0.0.0.0 --port 8000 \
     --max-model-len 32768 --api-key <secret>
```
Then put these in `.env`:
- `LLM_BASE_URL=https://<pod-id>-8000.proxy.runpod.net/v1`
- `LLM_API_KEY=<secret>`
- `LLM_MODEL=Qwen/Qwen3-VL-8B-Instruct` (the served model name)

How the client handles RunPod:
- **Streaming.** Responses are streamed because the RunPod proxy closes requests that stay silent for about 100 s.
- **Structured output.** It uses vLLM `response_format: json_schema` and falls back automatically to `guided_json`, then to prompt + validate if the server rejects it.
- **Validation.** Every answer is validated against a Pydantic schema, with one repair retry.
- **Cache.** Answers are cached on disk in `output/.llm_cache`.
- **Prompt size.** Prompts stay small, a few thousand tokens, because the model never receives the whole JSON. That JSON is about 70K tokens of page text; the model gets only the compared findings and short excerpts.

## How it works

```
JSON A, JSON B
 → ingest        validate core fields; expiring = earlier effective date
 → flatten       generic walk of every section; list items aligned by identity key (form_number, coverage, …),
                 never by position; classified into report sections by config/catalogue.yaml
 → diff          values, deltas, %, continuity gap, coverage parts, admitted status (code, rubric-driven)
 → forms         schedule by normalized form number; edition changes; verbatim wording diff per form
 → model         per-section impact / severity / "why it matters", section summaries, risk & confidence
 → cross-ref     §8 mid-term changes, §9 contract checklist, §10 client focus, §11 pending items
 → model         executive verdict, top-10 critical changes with themes and next steps, focus quotes
 → guards        schema checks, numbers must exist in the facts, quotes must be verbatim, risk floor/ceiling
 → build         ReportModel (all counts, KPIs, chips computed by code)
 → render        Jinja2 + CSS → Chromium → US Letter PDF (header/footer stamped with Inter)
```

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
python -m pytest                       # units, fixture, Halstead truth, 13 scenarios, model plumbing (mock server)
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
