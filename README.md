# hemepath-parsing

Parses bone marrow pathology report sections into structured data using Azure
OpenAI structured outputs, and writes a single flat CSV.

## Input

`data/sections.jsonl` — one JSON object per line, one specimen instance per line,
keyed by `(order_id, instance)`. Section fields (`biopsy`, `aspirate`, `flow`,
`cell_count`, `immunostains`, `specimen_header`) may be null; null sections are
skipped. `specimen_header` only applies to consult (outside-institution) specimens.

## Output columns

- **biopsy**: cellularity_pct, cellularity_category, blasts_pct, {megakaryocytes,erythroid,myeloid}_dysplastic, fibrosis_increased, fibrosis_grade, adequacy
- **aspirate**: blasts_pct, {megakaryocytes,erythroid,myeloid}_dysplastic, ring_sideroblasts, ring_sideroblasts_pct, adequacy
- **flow**: blasts_pct, adequacy, source
- **cell_count**: blasts_pct, mast_cells_pct
- **immunostains**: blasts_pct
- **specimen_header**: date (ISO `YYYY-MM-DD`), null for non-consult rows
- **final_dx**: category (AML, ALL, MDS, MPN, CML, MDS/MPN, CMML, Lymphoma, Myeloma, Solid, Negative, Other), status (overt, residual, remission, negative)

All fields are nullable. Clinical scope is myeloid neoplasms and ALL.

## Install

Requires Python 3.12.

**uv (recommended):**

```bash
uv sync
```

Prefix commands below with `uv run`.

**pip:**

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Configure

Copy the template and fill in your Azure values:

```bash
cp section_parser/config.yaml.example section_parser/config.yaml
```

`section_parser/config.yaml` is gitignored — it's never committed. Edit at least
`azure_openai.endpoint` and `azure_openai.deployment` (and `azure_openai.tenant_id`
if using `auth: browser`); everything else has a working default.

**Auth** (`azure_openai.auth`):

- `cli` (default) — uses your local `az login` session. Run `az login` first.
- `browser` — opens an interactive sign-in window; requires `tenant_id`.

The deployment must be a **gpt-5 reasoning-family model** (e.g. `gpt-5.4`).
Non-reasoning models (`gpt-4o`, `gpt-4.1`, etc.) are not supported.

Other config knobs (`section_parser/config.yaml`, under `processing:`):

| key | default | meaning |
|---|---|---|
| `max_concurrency` | 20 | max requests in flight at once |
| `target_rpm` | 2000 | requests/min ceiling |
| `target_tpm` | 200000 | tokens/min ceiling |
| `max_retries` | 5 | retry attempts before a cell is left for the next run |
| `retry_base_delay` | 2.0 | exponential backoff base, in seconds |
| `dedup` | true | parse identical section text once, reuse the result everywhere it appears |
| `log_level` | WARNING | console verbosity |

`sections:` lists which sections to parse/emit (omit to parse all).

## Run

```bash
uv run python -m section_parser.parse_sections --limit 5   # smoke test
uv run python -m section_parser.parse_sections             # full run
```

| flag | meaning |
|---|---|
| `--limit N` | only process the first N input rows (smoke test) |
| `--fresh` | ignore/remove the existing checkpoint and start over |
| `--concurrency N` | override `max_concurrency` |
| `--yes` | skip the confirmation prompt |
| `--config PATH` | use a different config file |
| `--log-level LEVEL` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` |
| `--quiet` | only log errors |
| `--log-file PATH` | also write logs (metadata only, never report text) to a file |

Output: `data/parsed_sections_<timestamp>.csv`, one row per `(order_id, instance)`.

Progress is checkpointed to `data/.checkpoint.jsonl`, so an interrupted run
resumes where it left off — completed cells aren't reprocessed. Editing a
section's prompt/schema or an input cell's text auto-reparses just that cell;
you generally don't need `--fresh` unless you want to force a full rerun.

## Troubleshooting

- **Auth error at startup** — run `az login` (or, for `auth: browser`, complete
  the sign-in window when it opens).
- **All-blank output on the first run, no errors** — the reasoning model likely
  spent its token budget on reasoning rather than output. Try a smaller/simpler
  input first, or raise `max_completion_tokens` in `parse_section()` in
  `section_parser/parse_sections.py`.
- **A lot of HTTP 429s in the run report** — lower `target_rpm`/`target_tpm` or
  `--concurrency` in your config.
- **Run is slow** — check the verdict line in the end-of-run report: it tells you
  whether `max_concurrency`, `target_rpm`/`target_tpm`, or the server itself
  (429s) is the binding constraint, so you know which knob to raise.
- **Config errors on startup** (missing file, unset/placeholder values, bad
  knob types) — the error message names the exact key to fix in
  `section_parser/config.yaml`.

## Notes

- `data/` is gitignored — it holds PHI and run artifacts and must never be committed.
- Logs never contain report text, MRNs, or cell content — only content-hash keys,
  exception type names, and numbers.
