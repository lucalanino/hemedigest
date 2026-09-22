# hemedigest

[![tests](https://github.com/lucalanino/hemedigest/actions/workflows/ci.yml/badge.svg)](https://github.com/lucalanino/hemedigest/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)

Parse free text from bone marrow pathology reports into a structured tabular format using Azure OpenAI.

## A quick reminder

This sends your report text to Azure OpenAI. If that text is PHI, take a
sec to make sure it is actually okay under your institution's policies and
applicable law before you point this at real patient data.

You'll need an Azure subscription with an AI Foundry resource for this to work.

---

- [Input](#input)
- [Output columns](#output-columns)
- [Schemas and prompts](#schemas-and-prompts)
- [Install](#install)
- [Configure](#configure)
- [Run](#run)
- [Development](#development)
- [Troubleshooting](#troubleshooting)

## Input

The parser expects JSONL: each line is one report, split into sections.
Set the path in `config.yaml` under `files.input_jsonl`.

The only required keys are the section(s) listed in `config.yaml` under
`sections:` — the raw text of that section, e.g. `biopsy`, `aspirate`,
`flow`, `cell_count`, `immunostains`, `specimen_header`, `final_dx`.
Nulls are allowed and skipped by default.

Every other key in a line is passed through to the output as-is.

`order_id` and `instance` get an extra job: the parser reads them to warn you
when specimens are ambiguous (an `order_id` that repeats with no `instance`
to tell the rows apart), and, with `dedup: false`, to key the checkpoint
per-row instead of by content. `instance` is what tells two rows under the
same `order_id` apart when a single order covers multiple specimens (e.g.
two biopsies). `instance` needs to be unique within an `order_id`, not globally.

## Output columns

Every non-section key from the input comes first, verbatim, followed by the
parsed fields for each enabled section:

- **biopsy**: cellularity_pct, cellularity_category (hypocellular, normocellular, hypercellular), blasts_pct, {megakaryocytes,erythroid,myeloid}_dysplastic, fibrosis_increased, fibrosis_grade, adequacy
- **aspirate**: blasts_pct, {megakaryocytes,erythroid,myeloid}_dysplastic, ring_sideroblasts, ring_sideroblasts_pct, adequacy
- **flow**: blasts_pct, adequacy, source
- **cell_count**: blasts_pct, mast_cells_pct
- **immunostains**: blasts_pct
- **specimen_header**: date (ISO `YYYY-MM-DD`)
- **final_dx**: category (AML, ALL, MDS, MPN, CML, MDS/MPN, CMML, Lymphoma, Myeloma, Solid, Negative, Other), status (overt, residual, remission, negative)

## Schemas and prompts

Each section's schema and prompt live together in one module under
`section_parser/schemas/`, e.g. `biopsy.py` has `SECTION_NAME`, `PROMPT`, and
`SCHEMA` (a pydantic `BaseModel` — one field per output column, with the
field's `description` doubling as the instruction the model sees for it).
`section_parser/schemas/__init__.py` collects every module into `SECTIONS`,
which is what the rest of the parser reads from — nothing else needs to
change when you add a section.

To edit an existing section, just change its schema fields or prompt text
directly in that module. To add a new one, copy an existing module as a
starting point, give it a unique `SECTION_NAME`, and register it in the
`_MODULES` tuple in `schemas/__init__.py`; then add that name to `sections:`
in `config.yaml` so it's actually parsed. Shared instructions that apply
across sections (scope, general extraction rules) live in
`schemas/_common.py`'s `COMMON_POLICY` — pull that into a new prompt rather
than repeating it.

**Editing a prompt or a schema does not invalidate the checkpoint** on its
own (see Run below) — pass `--fresh` after a change so already-checkpointed
cells reparse with the new schema/prompt instead of being served stale.

## Install

```bash
git clone https://github.com/lucalanino/hemedigest.git
cd hemedigest
```

Needs Python 3.12+.

With uv:

```bash
uv sync
```

Or with plain pip:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Configure

`section_parser/config.yaml.example` is the template config file with placeholder Azure values and defaults for everything else. Copy it
to `section_parser/config.yaml` and replace placeholders.

```bash
cp section_parser/config.yaml.example section_parser/config.yaml
```

At minimum, set `azure_openai.endpoint` and `azure_openai.deployment` (plus
`tenant_id` if you're using `auth: browser`).

Auth (`azure_openai.auth`):

- `cli` (default) requires an `az login` session.
- `browser` — pops open a sign-in window, needs `tenant_id` set.

The deployment has to be a gpt-5 reasoning model (`gpt-5-mini`, `gpt-5.4`).
Older models like `gpt-4o` won't work because they do not support `reasoning_effort`.
`endpoint` is the resource root (`https://<resource>.services.ai.azure.com`).

A few more `azure_openai:` knobs worth knowing about:

- `reasoning_effort` — `none`, `minimal`, `low` (default), `medium`, `high`,
  or `xhigh`. Higher effort tends to get you more accurate parses at the cost
  of more reasoning tokens. Changing it invalidates the checkpoint.
- `api_version` and `scope` — these ship with working defaults in the
  template; you shouldn't need to touch them unless Azure asks you to.

Other knobs live under `processing:` in the same file:

| key | default | meaning |
|---|---|---|
| `max_concurrency` | 5 | max requests in flight at once |
| `max_retries` | 5 | retries per cell before it's left for the next run |
| `retry_base_delay` | 2.0 | backoff base, in seconds |
| `dedup` | true | parse identical section text once, reuse it everywhere it shows up |
| `log_level` | WARNING | `DEBUG` while tuning concurrency, `WARNING` for a quiet run |

`sections:` is required to pick which sections get parsed/emitted, and
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
The directory and filename prefix come from `files.output_dir` and
`files.output_prefix` if you want them somewhere else; same for the
checkpoint file's path (`files.checkpoint`).

Runs are checkpointed to `data/.checkpoint.jsonl`, so if one gets interrupted
it'll just pick back up — already-done cells aren't reprocessed. Change an
input cell's text and that cell reparses, since the checkpoint key is a hash of
the text itself.

**Editing a prompt or a schema does not invalidate the checkpoint** — the
fingerprint covers only `dedup`, `reasoning_effort` and `deployment`. Pass
`--fresh` after touching anything under `section_parser/schemas/`.

## Development

```
section_parser/     the package: CLI, schemas, prompts
tests/              offline suite (no network) + opt-in live tests
data/               inputs, outputs, checkpoint — gitignored
```

`uv sync` alone only installs the runtime deps; pytest and ruff are a
separate `dev` group, opt in with:

```bash
uv sync --group dev
```

Tests:

```bash
uv run pytest             # offline, free, no Azure needed
uv run pytest -m live     # 4 real calls against your deployment
```

The live tests are deselected by default via `addopts` in `pyproject.toml`, and
they *skip* when `config.yaml` or an `az login` session is missing.

## Troubleshooting

**Auth error at startup** — run `az login`, or if you're on `auth: browser`,
finish the sign-in window when it pops up.

**Every call fails, nothing parses** — the run report's `Non-retryable 4xx`
line will be non-zero and the logged error names the cause: a 404 usually means
the endpoint carries an API path or the deployment name is wrong, a 401/403
means the identity lacks a role on the resource.

**Lots of HTTP 429s in the run report** — turn down `--concurrency`. Retries
honour the server's `Retry-After` when it sends one, so a few 429s cost little;
sustained ones mean you're above the deployment's quota.

**Run feels slow** — the end-of-run report's verdict line says whether
concurrency or the server was the bottleneck. Peak in-flight at the max with no
429s means `--concurrency` is worth raising.

**Config errors at startup** — the error message names the exact key in
`config.yaml` that's missing or wrong.
