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
placeholders. You can supply the real values either way:

### Option A — edit the YAML

Edit `section_parser/config.yaml` and replace the placeholders:

```yaml
azure_openai:
  endpoint: "https://<resource>.openai.azure.com/"
  deployment: "<your gpt-5.4 deployment name>"
  api_version: "2024-12-01-preview"
  tenant_id: "<your tenant id>"
  scope: "https://cognitiveservices.azure.com/.default"
```

### Option B — environment variables (override the YAML; recommended)

If set, these take precedence over the YAML values:

```bash
export AZURE_OPENAI_ENDPOINT="https://<resource>.openai.azure.com/"
export AZURE_OPENAI_DEPLOYMENT="<your gpt-5.4 deployment name>"
export AZURE_TENANT_ID="<your tenant id>"
```

```powershell
# Windows PowerShell
$env:AZURE_OPENAI_ENDPOINT = "https://<resource>.openai.azure.com/"
$env:AZURE_OPENAI_DEPLOYMENT = "<your gpt-5.4 deployment name>"
$env:AZURE_TENANT_ID = "<your tenant id>"
```

The run aborts with a clear message if `endpoint`, `deployment`, or `tenant_id` is
still unset or left as a placeholder.

**Authentication** is interactive browser-based Azure AD (no API key); a browser
window opens on the first call. The token is cached and refreshed for the run.

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
instance)`; `final_dx_category` / `final_dx_status` are per instance, deduped so an
identical diagnosis repeated across a report's instances is parsed only once).

**Resume:** progress is checkpointed to `data/.checkpoint.jsonl` (a stable path, not
timestamped), so an interrupted run continues where it left off. Failed cells are
re-queued on the next run; successful parses are not re-done. Use `--fresh` to
discard the checkpoint and reprocess everything.

> First-run tip: if `--limit 5` returns all-blank rows with **no** error, the
> reasoning model likely spent its output budget on reasoning. Raise
> `max_completion_tokens` in `parse_section()` (the single isolated API call in
> `section_parser/parse_sections.py`).

## Notes

- `data/` is gitignored — it holds PHI and run artifacts and must never be committed.
- Rate limiting is gentle by default (config `processing`: concurrency 8, targets
  2000 RPM / 200k TPM against the 2500 / 250k deployment limits), with 429 backoff.
