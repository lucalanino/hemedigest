# hemepath-parsing

Parses bone marrow pathology report sections into structured data with Azure
OpenAI structured outputs, and writes it all out as one flat CSV.

## Input

`data/sections.jsonl` — one specimen instance per line, keyed by `(order_id,
instance)`. Section fields (`biopsy`, `aspirate`, `flow`, `cell_count`,
`immunostains`, `specimen_header`) can be null; null ones are just skipped.
`specimen_header` only shows up on consult (outside-institution) specimens.

`data/` is gitignored — it holds PHI, so it should never end up in git.

## Output columns

- **biopsy**: cellularity_pct, cellularity_category, blasts_pct, {megakaryocytes,erythroid,myeloid}_dysplastic, fibrosis_increased, fibrosis_grade, adequacy
- **aspirate**: blasts_pct, {megakaryocytes,erythroid,myeloid}_dysplastic, ring_sideroblasts, ring_sideroblasts_pct, adequacy
- **flow**: blasts_pct, adequacy, source
- **cell_count**: blasts_pct, mast_cells_pct
- **immunostains**: blasts_pct
- **specimen_header**: date (ISO `YYYY-MM-DD`), null on non-consult rows
- **final_dx**: category (AML, ALL, MDS, MPN, CML, MDS/MPN, CMML, Lymphoma, Myeloma, Solid, Negative, Other), status (overt, residual, remission, negative)

Everything's nullable. Scope is myeloid neoplasms and ALL.

## Install

Needs Python 3.12.

With uv:

```bash
uv sync
```

Prefix commands below with `uv run`. Or with plain pip:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Configure

```bash
cp section_parser/config.yaml.example section_parser/config.yaml
```

`config.yaml` is gitignored, so this is where your real Azure values live. At
minimum, set `azure_openai.endpoint` and `azure_openai.deployment` (plus
`tenant_id` if you're using `auth: browser`) — the rest of the template already
has sane defaults.

Auth (`azure_openai.auth`):

- `cli` (default) — uses your `az login` session. Run that first.
- `browser` — pops open a sign-in window; needs `tenant_id` set.

The deployment has to be a gpt-5 reasoning model (`gpt-5.4` etc.) — older
models like `gpt-4o` won't work here.

Other knobs live under `processing:` in the same file:

| key | default | meaning |
|---|---|---|
| `max_concurrency` | 20 | max requests in flight at once |
| `target_rpm` | 2000 | requests/min ceiling |
| `target_tpm` | 200000 | tokens/min ceiling |
| `max_retries` | 5 | retries per cell before it's left for the next run |
| `retry_base_delay` | 2.0 | backoff base, in seconds |
| `dedup` | true | parse identical section text once, reuse it everywhere it shows up |
| `log_level` | WARNING | console verbosity |

`sections:` picks which sections get parsed/emitted — leave it out to parse
everything.

## Run

```bash
uv run python -m section_parser.parse_sections --limit 5   # smoke test
uv run python -m section_parser.parse_sections             # full run
```

| flag | meaning |
|---|---|
| `--limit N` | only process the first N rows (smoke test) |
| `--fresh` | drop the existing checkpoint and start over |
| `--concurrency N` | override `max_concurrency` |
| `--yes` | skip the confirmation prompt |
| `--config PATH` | use a different config file |
| `--log-level LEVEL` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` |
| `--quiet` | only log errors |
| `--log-file PATH` | also write logs to a file (metadata only, never report text) |

Output lands at `data/parsed_sections_<timestamp>.csv`, one row per
`(order_id, instance)`.

Runs are checkpointed to `data/.checkpoint.jsonl`, so if one gets interrupted
it'll just pick back up — already-done cells aren't reprocessed. Edit a
section's prompt/schema or an input cell's text and only that cell reparses.
You shouldn't need `--fresh` unless you actually want to force a full rerun.

## Troubleshooting

**Auth error at startup** — run `az login`, or if you're on `auth: browser`,
finish the sign-in window when it pops up.

**Everything comes back blank on the first run, no errors** — the model
probably burned its token budget on reasoning instead of output. Try a
smaller/simpler input, or add a `max_completion_tokens=...` argument to the
`client.chat.completions.parse(...)` call in `parse_section()` in
`section_parser/parse_sections.py` (it uses the SDK default today).

**Lots of HTTP 429s in the run report** — turn down `target_rpm`/`target_tpm`
or `--concurrency`.

**Run feels slow** — the end-of-run report prints a verdict line telling you
whether concurrency, the rate targets, or the server itself is the actual
bottleneck, so you know what to raise.

**Config errors at startup** — the error message names the exact key in
`config.yaml` that's missing or wrong.
