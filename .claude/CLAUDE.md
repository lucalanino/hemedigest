# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Parses free-text bone marrow pathology report sections (biopsy, aspirate, flow,
cell_count, immunostains, specimen_header, final_dx) into a structured CSV using
Azure OpenAI structured outputs (gpt-5-class reasoning models only — older models
like gpt-4o lack `reasoning_effort` support). Input is PHI-adjacent free text sent
to Azure OpenAI, so treat report text as sensitive throughout.

## Commands

```bash
uv sync --group dev                                          # install runtime + dev deps (pytest, ruff)
uv run pytest                                                 # offline test suite (no network, no Azure)
uv run pytest -m live                                         # 4 real calls against the configured deployment (costs money, needs az login + config.yaml)
uv run pytest tests/test_checkpoint.py::test_name             # run a single test
uv run ruff check .                                           # lint

uv run python -m section_parser.parse_sections --limit 5      # smoke test against real input
uv run python -m section_parser.parse_sections                # full run
uv run python -m section_parser.parse_sections --fresh --yes  # discard checkpoint and reprocess everything, non-interactive
```

Live tests skip automatically when `section_parser/config.yaml` or an `az login`
session is missing, and are excluded by default via `addopts` in `pyproject.toml`.

Before running the parser, `section_parser/config.yaml` must exist (copy from
`config.yaml.example`) with real `azure_openai.endpoint` / `azure_openai.deployment`.

## Architecture

**Section registry (`section_parser/schemas/`)**: each section is one module
(`biopsy.py`, `aspirate.py`, etc.) exporting `SECTION_NAME`, `PROMPT`, and `SCHEMA`
(a pydantic `BaseModel` whose field `description`s double as per-field instructions
to the model). `schemas/__init__.py`'s `_MODULES` tuple registers every module into
`SECTIONS`, the single source of truth the rest of the pipeline reads from — adding
a section means writing the module, registering it in `_MODULES`, and enabling it
in `config.yaml`'s `sections:` list. Shared prompt text lives in `schemas/_common.py`'s
`COMMON_POLICY`.

**Pipeline (`section_parser/parse_sections.py`)**: input JSONL rows are exploded into
one `WorkUnit` per non-empty (section, row) cell (`build_work_units`). Each unit gets
a content-addressed `key` (`unit_key`) combining a hash of the section's prompt+schema
(`section_fingerprint`) with a hash of the cell text — so with `dedup: true` (default),
byte-identical text anywhere in the input collapses to a single model call reused for
every row that has it, and editing a section's prompt/schema invalidates only that
section's cache. Units run concurrently through `run_unit` under an `asyncio.Semaphore`,
with per-unit retry/backoff (honoring the server's `Retry-After` on 429s) and a
taxonomy of terminal outcomes (refusal, length-truncated, content-filtered, fatal 4xx)
that are distinguished in the run report.

**Checkpointing**: every completed unit is appended to a JSONL checkpoint
(`CheckpointWriter`) tagged with a run-wide `schema_signature` (hash of `dedup` +
`reasoning_effort` + `deployment`). On load, records whose signature doesn't match
the current run are dropped as stale. This means changing `reasoning_effort`,
`deployment`, or `dedup` auto-invalidates the whole checkpoint, but editing a prompt
or schema field does *not* — that's caught separately by `section_fingerprint` baked
into each unit's `key`, so `--fresh` is required after touching anything under
`schemas/` for those cells to actually reparse.

**Auth**: `build_client` uses Azure AD (`AzureCliCredential` by default, or
`InteractiveBrowserCredential` with `auth: browser` + `tenant_id`), fetching a token
up front so a missing `az login` fails fast rather than deep inside the retry loop.
`max_retries=0` is set on the SDK client deliberately — `run_unit` owns all
retry/backoff; stacking the SDK's own retries on top caused multi-minute hangs.

**Output assembly**: `assemble_rows` re-walks the input rows and re-derives each
cell's checkpoint key to look up its parsed result — this key derivation must stay
in sync with `build_work_units`' empty-cell guard (`is_empty_cell`) or lookups
silently miss. Non-section input keys pass through to the CSV verbatim, in
first-seen order, ahead of the per-section parsed columns (`{section}_{field}`).

**Run stats (`section_parser/runlog.py`)**: `RunStats` counters feed
`format_run_report`, which prints a verdict identifying the binding constraint
(non-retryable 4xx > server throttling > concurrency-bound > unsaturated) — read
this instead of raising concurrency blind after a run "feels slow".
