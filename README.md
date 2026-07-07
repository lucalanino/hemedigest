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

Requires Python 3.12. Two interchangeable ways to install — pick whichever fits the
machine; both produce the same pinned dependency set.

### uv (recommended)

```bash
uv sync
```

Creates `.venv` and installs the exact versions from `pyproject.toml` / `uv.lock`.
Prefix commands with `uv run` (see [Usage](#usage)).

### pip

A pinned `requirements.txt` is committed at the repo root (generated from the same
lockfile), so no uv is needed:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

After any dependency change, refresh the lockfile and the exported `requirements.txt`
so the two installs stay in sync:

```bash
uv lock
uv export --format requirements-txt --no-hashes --no-emit-project --no-dev -o requirements.txt
```

Dev-only tools (e.g. `ruff` for formatting) live in `[dependency-groups].dev` — add
them with `uv add --dev <pkg>` and run via `uv run`. `--no-dev` keeps them out of the
exported `requirements.txt`, so the prod VM never installs them.

## Configuration

`section_parser/config.yaml` holds all runtime settings, including real Azure
values, and is **gitignored** — it's never committed. Only the placeholder
template, `section_parser/config.yaml.example`, is tracked in git.

Copy the template and fill in your real values:

```bash
cp section_parser/config.yaml.example section_parser/config.yaml
```

Then edit `azure_openai.endpoint` / `azure_openai.deployment` (and
`azure_openai.tenant_id` if `auth: browser`) in `section_parser/config.yaml` —
everything else in the template is already a sensible default (see
[Runtime knobs](#runtime-knobs) below).

The run aborts with a clear message if `config.yaml` is missing entirely, or if
`endpoint`/`deployment`/`tenant_id` is still unset or left as a placeholder.

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

### Runtime knobs

All runtime settings live in `section_parser/config.yaml` (see
[Configuration](#configuration) above for how to create it). Three blocks:

```yaml
files:                      # input/output paths
  input_jsonl: "data/sections.jsonl"
  output_dir: "data"
  output_prefix: "parsed_sections"
  checkpoint: "data/.checkpoint.jsonl"

sections:                   # which sections to parse/emit (omit block = all)
  - biopsy
  # ... see "Section selection" under Usage

processing:
  max_concurrency: 20       # in-flight request cap (the semaphore); --concurrency overrides
  target_rpm: 2000          # client-side requests-per-minute ceiling
  target_tpm: 200000        # client-side tokens-per-minute ceiling
  max_retries: 5            # retry attempts per work unit before it's re-queued next run
  retry_base_delay: 2.0     # exponential backoff base (seconds): delay = base * 2**attempt
  dedup: true               # parse byte-identical section text once, fan the result out
  log_level: "WARNING"      # console verbosity; --log-level / --quiet override
```

`max_concurrency`, `target_rpm`, and `target_tpm` are the throughput knobs — see
[Concurrency & the semaphore](#concurrency--the-semaphore) for how they compose and how
to tune them from the run report. `log_level` is covered under
[Logging & the run report](#logging--the-run-report); `dedup` under Usage.

## Concurrency & the semaphore

The parser fans every pending work unit out at once with `asyncio.gather`, and gates
them with **two independent throttles** (`RateLimiter` in `parse_sections.py`). They do
different jobs, and you need both:

- **The semaphore (`max_concurrency`)** caps how many requests are *in flight at once*.
  It protects the connection pool and memory. A gpt-5 reasoning call runs for tens of
  seconds, so rate pacing alone would let in-flight requests pile up into the hundreds
  before the first ones return — the semaphore is what stops that.
- **The sliding-window RPM/TPM limiter (`target_rpm` / `target_tpm`)** paces the
  *start rate* of new requests against your Azure deployment quota, over rolling 60-second
  windows. Tokens are estimated (~chars/4 + headroom) before the call.

**Why the ordering matters (and why it looks the way it does).** Each attempt takes a
semaphore slot *first*, then asks the limiter for rate budget *right before* the call
goes out. That's deliberate: reserving rate budget earlier — before a slot is free —
would let the 60s window fill up with requests that only actually leave later, in a
burst. The window would run ahead of reality, overshoot the real quota, and draw *more*
429s. Counting the request at the moment it's sent keeps the window honest. This is the
one non-obvious bit of the design; it's documented in the `RateLimiter` docstring so it
doesn't get "cleaned up" into a regression.

**Which knob to turn.** At the default settings the semaphore (20) is usually the
binding constraint — 20 in-flight reasoning calls come nowhere near 2000 RPM, so the
limiter rarely blocks. The end-of-run [run report](#logging--the-run-report) measures
this for you and prints a verdict:

- *semaphore binding* (peak in-flight pinned at `max_concurrency`, limiter idle) → raise
  `--concurrency` / `max_concurrency` to go faster;
- *rate limiter binding* (requests spending real time blocked, split RPM vs TPM) → raise
  the offending target if your quota allows;
- *server throttling* (429s returned) → you're past the real ceiling; lower the targets
  or concurrency. 429s are retried with exponential backoff; the server's `Retry-After`
  is logged but not yet honored (backoff is fixed exponential).

## Logging & the run report

Logging uses the stdlib `logging` module through a tqdm-safe handler (log lines never
corrupt the progress bar). Configure it with `processing.log_level` or the CLI flags
(`--log-level`, `--quiet`, `--log-file`); the CLI wins.

**Default (`WARNING`) is quiet on purpose** — you get the progress bar, plus only the
events that need attention as they happen: HTTP 429s (with the `Retry-After` the server
asked for) and exhausted-retry failures. This is the key fix over the old behavior,
where a transient 429 was retried silently and left no trace, so you couldn't tell you
were being throttled. Drop to `--log-level DEBUG` to also see every retry and each
refusal / recorded-null.

**PHI-safe by construction.** Log records only ever contain content-hash work-unit keys,
exception *type names*, and numbers — **never report text, MRNs, or cell content**, and
that holds even when an underlying exception's message is chatty (only its class name is
logged). `--log-file PATH` inherits the same guarantee (it creates the parent directory
and always records at DEBUG regardless of console level), so it's safe to keep on
long-running / prod-VM runs.

**The run report** prints at the end of every run that processed anything (always shown,
regardless of log level) and is the tool for tuning the knobs above:

- **Outcomes** — parsed OK · refusals · length-truncated nulls · content-filtered nulls ·
  failures (re-queued for next run) · cells skipped as empty/`NA`.
- **Throughput** — achieved RPM/TPM (whole-run average) vs. your targets, plus *actual*
  token usage from the API so TPM tuning isn't a guess. On short runs the average can
  read above target — the limiter only bounds any rolling 60s window, not a brief burst.
- **Limits** — peak in-flight vs. `max_concurrency`, how long requests waited on the local
  limiter (split RPM vs TPM), server 429 count — ending in the one-line verdict described
  in [Concurrency & the semaphore](#concurrency--the-semaphore).

Empty and placeholder cells (`null`, whitespace, or a sentinel like `NA` / `N/A` /
`None` / `.`) are skipped entirely — never sent to the model, so they cost nothing — and
the count is shown both before the confirmation prompt and in the run report.

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
| `--log-level LEVEL` | console log level: `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` (overrides `processing.log_level`) |
| `--quiet` | only log errors (shortcut for `--log-level ERROR`) |
| `--log-file PATH` | also append **metadata-only** logs to a file (never report text) |

Concurrency behavior and the throughput knobs are covered under
[Concurrency & the semaphore](#concurrency--the-semaphore); the logging flags and the
end-of-run run report under [Logging & the run report](#logging--the-run-report).

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

Matching is **byte-identical** only: cells differing by trailing whitespace, blank
lines, or punctuation count as distinct and are parsed separately. This is
deliberate — there is no normalization pass, since aggressive normalization risks
collapsing genuinely different specimens.

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
- Rate limiting is gentle by default (config `processing`: `max_concurrency: 20`,
  `target_rpm: 2000` / `target_tpm: 200000` against ~2500 / 250k deployment limits), with
  exponential 429 backoff. See [Concurrency & the semaphore](#concurrency--the-semaphore)
  for how to tune these from the run report.
