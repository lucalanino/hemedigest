# hemepath-parser

Parse bone marrow pathology reports into a table using Azure OpenAI structured outputs.

## Input

The parser expects JSONL: each line is one report, already split into sections.
Set the path in `config.yaml` under `files.input_jsonl`.

The only required keys are the section(s) listed in `config.yaml` under
`sections:` — the raw text of that section, e.g. `biopsy`, `aspirate`,
`flow`, `cell_count`, `immunostains`, `specimen_header`, `final_dx`. Any of
these can be null; null ones are just skipped.

Every other key in a line — `order_id`, `mrn`, whatever else your data has —
is passed through to the output as-is; nothing else is required.

`order_id` and `instance` get an extra job: the parser reads them to warn you
when specimens are ambiguous (an `order_id` that repeats with no `instance`
to tell the rows apart), and, with `dedup: false`, to key the checkpoint
per-row instead of by content. They don't have to be called that — point
`files.order_id_col` / `files.instance_col` at whatever your columns are
actually named.

## Output columns

Every non-section key from the input comes first, verbatim, followed by the
parsed fields for each enabled section:

- **biopsy**: cellularity_pct, cellularity_category, blasts_pct, {megakaryocytes,erythroid,myeloid}_dysplastic, fibrosis_increased, fibrosis_grade, adequacy
- **aspirate**: blasts_pct, {megakaryocytes,erythroid,myeloid}_dysplastic, ring_sideroblasts, ring_sideroblasts_pct, adequacy
- **flow**: blasts_pct, adequacy, source
- **cell_count**: blasts_pct, mast_cells_pct
- **immunostains**: blasts_pct
- **specimen_header**: date (ISO `YYYY-MM-DD`)
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

`config.yaml` is where your real Azure values live. At minimum, set
`azure_openai.endpoint` and `azure_openai.deployment` (plus `tenant_id` if
you're using `auth: browser`) — the rest of the template already has sane
defaults.

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
| `log_level` | WARNING | `DEBUG` for tuning concurrency/rate-limit knobs, `WARNING` for a quiet run |

`sections:` is required — it picks which sections get parsed/emitted, and
doubles as the list of columns your input must have (see Input above).

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
| `--log-level LEVEL` | `DEBUG` or `WARNING` (overrides config) |
| `--log-file PATH` | also write logs to a file (metadata only, never report text) |

Output lands at `data/parsed_sections_<timestamp>.csv`, one row per input line.

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
