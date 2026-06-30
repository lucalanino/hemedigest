# hemepath-parsing

Parses bone marrow pathology report sections into structured data using Azure
OpenAI structured outputs (one Pydantic schema + system prompt per section), and
writes a single flat CSV.

## Input

`data/sections.jsonl` — one JSON object per line = one specimen instance. Keyed by
`(order_id, instance)`. `final_dx` is per instance (a report with multiple specimens
can carry a different diagnosis on each; single-diagnosis reports just repeat the
same text across instances, which is parsed only once). Section fields (`biopsy`,
`aspirate`, `flow`, `cell_count`, `immunostains`, `specimen_header`) may be null;
null sections are skipped. `specimen_header` is the outside-institution header on
consult specimens.

## Sections & fields

All fields are nullable; numeric ranges take the larger value. Blast encoding is
section-specific (encoded in the schema field descriptions and the system prompts).
Clinical scope is myeloid neoplasms and ALL.

- **biopsy**: cellularity_pct, cellularity_category, blasts_pct, {megakaryocytes,erythroid,myeloid}_dysplastic, fibrosis_increased, fibrosis_grade, adequacy
- **aspirate**: blasts_pct, {megakaryocytes,erythroid,myeloid}_dysplastic, ring_sideroblasts, ring_sideroblasts_pct, adequacy
- **flow**: blasts_pct, adequacy, source
- **cell_count**: blasts_pct, mast_cells_pct
- **immunostains**: blasts_pct
- **specimen_header**: date — the date on a consult specimen's outside-institution header (mm/dd/yy or mm/dd/yyyy, typos repaired against a ~1995–2026 range), normalized to ISO `YYYY-MM-DD`. Only consult rows carry this; null otherwise.
- **final_dx** (per instance): category ∈ {AML, ALL, MDS, MPN, CML, MDS/MPN, CMML, Lymphoma, Myeloma, Solid, Negative, Other}, status ∈ {overt, residual, remission, negative}. Classified from the diagnosis on the analyzed specimen only — history and concurrent diagnoses do not change it; a specimen with no morphologic disease (incl. remission) is Negative.

## Installation

### Development (uv)

```bash
uv sync
```

This creates `.venv` and installs all dependencies from `pyproject.toml` /
`uv.lock`. Run commands with `uv run` (see Usage).

### Production VM (pip, no uv)

A pinned `requirements.txt` is committed at the repo root. Clone the repo, then:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Regenerate `requirements.txt` after any dependency change:

```bash
uv export --format requirements-txt --no-hashes --no-emit-project -o requirements.txt
```

## Configuration

Real Azure values are **not** committed — `section_parser/config.yaml` ships with
placeholders. Supply the real values via a **gitignored local overlay**:

### Local overlay (recommended)

Copy the example and fill in your values:

```bash
cp section_parser/config.local.yaml.example section_parser/config.local.yaml
```

```yaml
# section_parser/config.local.yaml  (gitignored — never committed)
azure_openai:
  endpoint: "https://<resource>.openai.azure.com/"
  deployment: "<your gpt-5.4 deployment name>"
  tenant_id: "<your tenant id>"   # only needed for auth: browser
```

The overlay is deep-merged on top of `config.yaml` at load (overlay wins), so the
committed file keeps placeholders while real values stay local. Only the keys you
override need to be present. If you prefer, you can edit `config.yaml` directly
instead — but then keep it untracked so secrets aren't committed.

The run aborts with a clear message if `endpoint` or `deployment` (and `tenant_id`
when `auth: browser`) is still unset or left as a placeholder.

### Authentication

Azure AD, no API key. Two strategies, selected by `azure_openai.auth`:

- **`cli`** (default) — uses your local `az login` session via `AzureCliCredential`.
  Run `az login` first; `tenant_id` is then optional (taken from the active session).
- **`browser`** — opens an interactive sign-in window (`InteractiveBrowserCredential`)
  and requires `tenant_id`. Use it where CLI auth isn't available (e.g. the prod VM,
  which has no usable CLI credential).

Auth is probed once at startup, so a missing `az login` (or a cancelled browser
sign-in) fails fast with an actionable message instead of stalling mid-run.

### API surface & version pinning

This project deliberately runs on the **classic dated-`api-version` surface**:
`AsyncAzureOpenAI` + `azure_endpoint` + a pinned `api_version` (`2024-12-01-preview`),
authenticating against the `cognitiveservices.azure.com` token scope. This is a
prod-stability choice — the deployment is validated against this exact version, and
structured outputs have been supported since `2024-08-01-preview`. We pin a dated
version rather than the rolling `preview` alias because that alias was observed to
stall `chat.completions.parse` on the VM.

Note that the official docs now default to the newer **v1 API**, and the supported-
models table there trails real availability (it lists up to `gpt-5.1`, yet `gpt-5.4`
works with structured outputs in practice — the table is curated and lags). Staying
on the classic surface is intentional, not a limitation.

Porting to v1 is straightforward **if your resource exposes it**:

- swap `AsyncAzureOpenAI(azure_endpoint=…, api_version=…)` for the `OpenAI`/`AsyncOpenAI`
  client with `base_url="https://<resource>.openai.azure.com/openai/v1/"`,
- drop `api_version` entirely,
- switch the token scope to **`https://ai.azure.com/.default`** (the classic
  `cognitiveservices.azure.com` scope will fail auth against v1).

v1 access can be gated by the resource kind or by Azure Policy restricting allowed
`api-version` values — i.e. it's an org/governance setting, not a single toggle — so
confirm with your platform team before assuming it's available. `format`/`pattern`
JSON-schema keywords are unsupported on **both** surfaces, which is why date fields
are typed as `str` + a validator rather than `datetime.date` (see
`section_parser/schemas/specimen_header.py`).

### Model family

This project targets the **gpt-5 reasoning family** (e.g. `gpt-5.4`) only, and that
assumption is baked in: `reasoning_effort` is sent on every call, and the
reasoning-vs-output token-budget behavior described in the first-run tip applies only
to reasoning models. Older / non-reasoning deployments (`gpt-4o`, `gpt-4.1`, etc.)
are **not supported** — they reject `reasoning_effort`, so point the deployment at a
gpt-5-family model.

## Usage

```bash
# uv
uv run python -m section_parser.parse_sections --limit 5   # smoke test (first 5 rows)
uv run python -m section_parser.parse_sections             # full run

# pip / activated venv
python -m section_parser.parse_sections --limit 5
python -m section_parser.parse_sections
```

Flags:

| flag | meaning |
|------|---------|
| `--limit N` | only process the first N input rows (smoke test) |
| `--fresh` | ignore/remove the existing checkpoint and start over |
| `--concurrency N` | override max in-flight requests |
| `--yes` | skip the confirmation prompt |
| `--config PATH` | use a different config file |

**Section selection:** the `sections` list in `section_parser/config.yaml` controls
which sections are parsed and emitted, even when the input file contains all of them.
Comment out or remove any of `biopsy`, `aspirate`, `flow`, `cell_count`,
`immunostains`, `specimen_header`, `final_dx` to skip it (skipped sections produce no
work units and no output columns). Omit the block entirely to parse all sections.
Toggling sections does not invalidate the checkpoint, so you can run a subset and add
more later without re-parsing the sections already done.

**Output:** `data/parsed_sections_<timestamp>.csv` (one row per `(order_id,
instance)`).

**Dedup:** by default (`processing.dedup: true`) the parser content-addresses work
by section text: **byte-identical text anywhere in the file is parsed once and the
result fanned out** to every cell that shares it. This is lossless — the model only
ever sees the section prompt + the section text, so identical input yields the same
output — and it collapses boilerplate, "SEE ABOVE" stubs, repeated outside-institution
headers, and a single diagnosis repeated across a report's instances down to one API
call. The run summary reports how many cells were merged. Set `dedup: false` to parse
every cell separately (change-detection only). Toggling the flag changes the
checkpoint keys, so it invalidates an existing checkpoint (records re-queue with a
warning).

**Resume:** progress is checkpointed to `data/.checkpoint.jsonl` (a stable path, not
timestamped), so an interrupted run continues where it left off. Failed cells are
re-queued on the next run; successful parses are not re-done. Checkpoint keys are
content-addressed (they hash the section text), so if a cell's **source text
changes**, that cell reparses automatically while unchanged cells stay skipped —
no `--fresh` needed. Use `--fresh` to discard the checkpoint and reprocess
everything regardless.

The checkpoint also fingerprints **what produces** each result, so code/config
changes invalidate the right cells automatically — you generally never need `--fresh`
for them:

- A section's **prompt and schema** (the schema exactly as sent to the model —
  field names, types, enums, and any `Field` descriptions) are hashed into that
  section's keys, so editing one section auto-reparses **only that section**.
- **`reasoning_effort`** and the **deployment/model** are in the global signature,
  so changing either auto-reparses **everything** (with the usual stale-record
  warning).

Not fingerprinted: `api_version` (an API-surface pin that doesn't change extraction)
and, of course, anything outside the program. `--fresh` remains the manual override.

> First-run tip: if `--limit 5` returns all-blank rows with **no** error, the
> reasoning model likely spent its output budget on reasoning. We deliberately leave
> the output cap at the SDK default rather than exposing it as a config knob; if you
> want to tune it, raise `max_completion_tokens` directly in `parse_section()` (the
> single isolated API call in `section_parser/parse_sections.py`). Note that on a
> reasoning model this cap covers reasoning **and** visible output together, so it's
> a lever to raise, not lower.

## Notes

- `data/` is gitignored — it holds PHI and run artifacts and must never be committed.
- Rate limiting is gentle by default (config `processing`: concurrency 8, targets
  2000 RPM / 200k TPM against the 2500 / 250k deployment limits), with 429 backoff.
