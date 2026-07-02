"""Parse bone marrow report sections via Azure OpenAI structured outputs.

Reads a .jsonl file formatted as one specimen instance per line, parses each
non-null section with its own schema + system prompt, and writes one flat,
timestamped CSV.

Auth is interactive browser-based Azure AD. Calls run concurrently with gentle
rate limiting, and progress is checkpointed so an interrupted run can resume.

Usage:
    python -m section_parser.parse_sections [--config PATH] [--limit N]
                                            [--fresh] [--concurrency N] [--yes]
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml
from azure.identity import (
    AzureCliCredential,
    InteractiveBrowserCredential,
    get_bearer_token_provider,
)
from openai import (
    AsyncAzureOpenAI,
    ContentFilterFinishReasonError,
    LengthFinishReasonError,
    RateLimitError,
)
from pydantic import BaseModel
from tqdm import tqdm

from section_parser import prompts
from section_parser.runlog import (
    RunStats,
    format_run_report,
    get_logger,
    setup_logging,
)

logger = get_logger()
from section_parser.schemas import (
    AspirateSchema,
    BiopsySchema,
    CellCountSchema,
    FinalDxSchema,
    FlowSchema,
    ImmunostainsSchema,
    SpecimenHeaderSchema,
)

# Report sections configuration ----

FINAL_DX_KEY = "final_dx"

# Every section is emitted at the (order_id, instance) grain, in output column
# order, and treated uniformly -- no per-section special cases. Work-unit keys are
# content-addressed (see unit_key): with dedup on (default) byte-identical text
# anywhere in the file is parsed once and fanned out, which also collapses a single
# diagnosis repeated across a report's instances down to one call.
INSTANCE_SECTIONS: dict[str, tuple[type[BaseModel], str]] = {
    "biopsy": (BiopsySchema, prompts.BIOPSY_PROMPT),
    "aspirate": (AspirateSchema, prompts.ASPIRATE_PROMPT),
    "flow": (FlowSchema, prompts.FLOW_PROMPT),
    "cell_count": (CellCountSchema, prompts.CELL_COUNT_PROMPT),
    "immunostains": (ImmunostainsSchema, prompts.IMMUNOSTAINS_PROMPT),
    "specimen_header": (SpecimenHeaderSchema, prompts.SPECIMEN_HEADER_PROMPT),
    FINAL_DX_KEY: (FinalDxSchema, prompts.FINAL_DX_PROMPT),
}

# Every section the parser knows how to handle, in output order.
ALL_SECTIONS: list[str] = list(INSTANCE_SECTIONS)

# Passthrough identity columns, carried verbatim from input to output. Names match
# the input file's column headers exactly (see check_id_columns for the presence
# guard). ``instance`` is optional; the rest are mandatory.
ID_COLUMNS = ["order_id", "mrn", "description", "sample_date", "instance"]
OPTIONAL_ID_COLUMNS = {"instance"}

# Cell values treated as empty: skipped (never sent to the model, so they cost
# nothing) and emitted blank in the CSV. JSON null and whitespace-only are always
# empty; in addition these placeholder tokens are, matched case-insensitively on the
# *whole* stripped cell (so real text containing "na" mid-sentence is untouched).
# Kept in code, not config: it's an internal normalization mechanic (see TODO.md).
EMPTY_SENTINELS = {"na", "n/a", "none", "nil", "null", "-", "--", "."}


def is_empty_cell(value: Any) -> bool:
    """True if a section cell carries no parseable content (null/blank/placeholder)."""
    if value is None:
        return True
    stripped = str(value).strip()
    return not stripped or stripped.lower() in EMPTY_SENTINELS


def selected_instance_sections(
    enabled: list[str],
) -> dict[str, tuple[type[BaseModel], str]]:
    """Instance-level sections to parse, preserving canonical order."""
    return {k: v for k, v in INSTANCE_SECTIONS.items() if k in enabled}

# Client configuration ----


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``overlay`` onto ``base`` in place; overlay wins.

    Recurse only when both sides are dicts; lists and scalars are replaced
    wholesale (so an overlay ``sections:`` list overrides, not appends).
    """
    for key, value in overlay.items():
        if (
            isinstance(value, dict)
            and isinstance(base.get(key), dict)
        ):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _is_placeholder(value: Any) -> bool:
    return not value or (isinstance(value, str) and value.startswith("<"))


def load_config(config_path: str) -> dict[str, Any]:
    """Load YAML config, merging an optional gitignored ``*.local.yaml`` overlay.

    Real values (endpoint/deployment/tenant_id) live in the gitignored overlay
    next to the committed config (``config.yaml`` -> ``config.local.yaml``); the
    overlay is deep-merged on top so the committed file can keep placeholders.
    """
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    cfg = Path(config_path)
    overlay_path = cfg.with_name(f"{cfg.stem}.local{cfg.suffix}")
    if overlay_path.exists():
        with open(overlay_path, "r", encoding="utf-8") as f:
            overlay = yaml.safe_load(f)
        if isinstance(overlay, dict):
            _deep_merge(config, overlay)

    az = config["azure_openai"]

    # Auth strategy: Azure CLI by default (uses the `az login` session); browser
    # is the explicit opt-in for environments where CLI auth is unavailable.
    az.setdefault("auth", "cli")
    if az["auth"] not in {"cli", "browser"}:
        raise SystemExit(
            f"Azure config 'auth' must be 'cli' or 'browser'; got {az['auth']!r}."
        )

    # endpoint/deployment are always required; tenant_id only for browser auth
    # (CLI auth derives the tenant from the active `az login` session).
    required = ["endpoint", "deployment"]
    if az["auth"] == "browser":
        required.append("tenant_id")
    for field in required:
        if _is_placeholder(az.get(field, "")):
            raise SystemExit(
                f"Azure config '{field}' is unset/placeholder. Set it in "
                f"'{overlay_path.name}' (gitignored) or '{cfg.name}'."
            )

    # gpt-5.x reasoning effort. 'minimal' is unsupported on 5.1+; omit to use the
    # model default. Validate early so a typo fails before any API call.
    az.setdefault("reasoning_effort", "low")
    valid_efforts = {"none", "minimal", "low", "medium", "high", "xhigh"}
    if az["reasoning_effort"] not in valid_efforts:
        raise SystemExit(
            f"Azure config 'reasoning_effort' must be one of {sorted(valid_efforts)}; "
            f"got {az['reasoning_effort']!r}."
        )

    # Section selection: omitted -> parse all; otherwise parse the listed subset,
    # de-duplicated and re-ordered to the canonical ALL_SECTIONS order.
    sections = config.get("sections")
    if sections is None:
        sections = list(ALL_SECTIONS)
    elif not isinstance(sections, list):
        raise SystemExit("Config 'sections' must be a list of section names.")
    unknown = [s for s in sections if s not in ALL_SECTIONS]
    if unknown:
        raise SystemExit(
            f"Config 'sections' has unknown name(s) {unknown}; "
            f"valid sections are {ALL_SECTIONS}."
        )
    selected = [s for s in ALL_SECTIONS if s in sections]
    if not selected:
        raise SystemExit("Config 'sections' is empty; list at least one section.")
    config["sections"] = selected

    # Global content-dedup: collapse byte-identical section text to one API call.
    proc = config["processing"]
    proc.setdefault("dedup", True)
    if not isinstance(proc["dedup"], bool):
        raise SystemExit(
            f"Config 'processing.dedup' must be true or false; got {proc['dedup']!r}."
        )

    # Default console log level (CLI --log-level/--quiet override at runtime).
    proc.setdefault("log_level", "WARNING")
    level = str(proc["log_level"]).upper()
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if level not in valid_levels:
        raise SystemExit(
            f"Config 'processing.log_level' must be one of {sorted(valid_levels)}; "
            f"got {proc['log_level']!r}."
        )
    proc["log_level"] = level
    return config


def build_client(az: dict[str, Any]) -> AsyncAzureOpenAI:
    """Build the async client using Azure AD auth (CLI by default, browser opt-in)."""
    tenant_id = az.get("tenant_id", "")
    has_tenant = not _is_placeholder(tenant_id)
    if az["auth"] == "browser":
        credential = InteractiveBrowserCredential(tenant_id=tenant_id)
    elif has_tenant:
        credential = AzureCliCredential(tenant_id=tenant_id)
    else:
        credential = AzureCliCredential()

    # Credentials authenticate lazily, so without this probe a missing `az login`
    # would surface only deep inside run_unit's retry loop as a generic
    # "[failed, will retry]" after a minute of backoff. Fetch a token up front so
    # auth problems fail fast with an actionable message.
    try:
        credential.get_token(az["scope"])
    except Exception as exc:  # noqa: BLE001 - turn any auth failure into a clear exit
        hint = (
            "Run `az login` (and `az account set --subscription ...` if needed)."
            if az["auth"] == "cli"
            else "Complete the browser sign-in when prompted."
        )
        raise SystemExit(
            f"Azure authentication failed ({type(exc).__name__}): {exc}\n{hint}"
        )

    token_provider = get_bearer_token_provider(credential, az["scope"])
    return AsyncAzureOpenAI(
        azure_endpoint=az["endpoint"],
        azure_ad_token_provider=token_provider,
        api_version=az["api_version"],
        # No SDK-level retries: run_unit already retries with backoff. Stacking
        # the SDK's silent retries on top turned a failing call into a multi-minute
        # apparent hang, so we let our own loop own retry/backoff.
        max_retries=0,
    )


# Model call ----


@dataclass
class CallOutcome:
    """Result of one model call.

    ``refused`` distinguishes a model refusal (a recorded null we want to *count* as
    such) from a genuine parsed result; ``tokens`` is the *actual* usage reported by
    the API (0 if the API omitted it) so the run report can show achieved TPM vs the
    target instead of guessing from the estimate.
    """

    parsed: Optional[BaseModel]
    refused: bool
    tokens: int


async def parse_section(
    client: AsyncAzureOpenAI,
    deployment: str,
    system_prompt: str,
    text: str,
    schema: type[BaseModel],
    reasoning_effort: str,
) -> CallOutcome:
    """Run one structured-output call, returning the parse plus refusal/usage metadata."""
    completion = await client.chat.completions.parse(
        model=deployment,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        response_format=schema,
        reasoning_effort=reasoning_effort,
    )
    usage = getattr(completion, "usage", None)
    tokens = getattr(usage, "total_tokens", 0) or 0
    message = completion.choices[0].message
    if getattr(message, "refusal", None):
        return CallOutcome(parsed=None, refused=True, tokens=tokens)
    return CallOutcome(parsed=message.parsed, refused=False, tokens=tokens)


# Rate limiting ----


class RateLimiter:
    """Two distinct throttles, both needed (see run_unit for how they compose):

    - ``sem`` (semaphore) caps *in-flight* requests -- it protects the connection
      pool / memory. Each reasoning call runs tens of seconds, so RPM pacing alone
      would let in-flight requests balloon into the hundreds.
    - the sliding 60s RPM/TPM windows pace the *start rate* against the Azure quota.

    ``acquire`` is deliberately called *inside* the semaphore and right before the
    call, so a request is counted against the window at the moment it actually goes
    out. Reserving rate budget earlier (e.g. before waiting for a slot) would let the
    window run ahead of reality, releasing a burst later -> overshoot -> more 429s.
    Keep this order.

    The blocked_* / *_blocks counters are read only by the end-of-run report to show
    whether the local limiter (vs the semaphore or server 429s) is the binding
    constraint; they never gate behavior.
    """

    def __init__(self, max_concurrency: int, target_rpm: int, target_tpm: int):
        self.sem = asyncio.Semaphore(max_concurrency)
        self.target_rpm = target_rpm
        self.target_tpm = target_tpm
        self._requests: deque[float] = deque()
        self._tokens: deque[tuple[float, int]] = deque()
        self._lock = asyncio.Lock()
        # Observability only (report), not control:
        self.blocked_events = 0  # acquires that had to wait at least once
        self.blocked_seconds = 0.0  # total wall time spent waiting in acquire
        self.rpm_blocks = 0  # times the RPM predicate tripped
        self.tpm_blocks = 0  # times the TPM predicate tripped

    def _purge(self, now: float) -> None:
        cutoff = now - 60.0
        while self._requests and self._requests[0] < cutoff:
            self._requests.popleft()
        while self._tokens and self._tokens[0][0] < cutoff:
            self._tokens.popleft()

    async def acquire(self, est_tokens: int) -> None:
        """Block until issuing a request with ``est_tokens`` stays within limits."""
        # Clamp so an oversized request still passes an empty window (no infinite spin).
        est_tokens = min(est_tokens, self.target_tpm)
        wait_start: Optional[float] = None
        while True:
            async with self._lock:
                now = time.monotonic()
                self._purge(now)
                tok_sum = sum(t for _, t in self._tokens)
                rpm_ok = len(self._requests) < self.target_rpm
                tpm_ok = tok_sum + est_tokens <= self.target_tpm
                if rpm_ok and tpm_ok:
                    self._requests.append(now)
                    self._tokens.append((now, est_tokens))
                    if wait_start is not None:
                        self.blocked_events += 1
                        self.blocked_seconds += now - wait_start
                    return
                # Record which ceiling forced the wait (both may trip at once).
                if not rpm_ok:
                    self.rpm_blocks += 1
                if not tpm_ok:
                    self.tpm_blocks += 1
                if wait_start is None:
                    wait_start = now
            await asyncio.sleep(0.5)


def estimate_tokens(system_prompt: str, text: str) -> int:
    """Rough token estimate (~chars/4) + headroom with intentional overestimation (800)"""
    return (len(system_prompt) + len(text)) // 4 + 800


# Checkpointing ----


def schema_signature(dedup: bool, reasoning_effort: str, deployment: str) -> str:
    """Global checkpoint fingerprint: run-wide settings that affect every result.

    Stored on every record; a mismatch purges stale records (with a warning) on
    load, instead of silently reusing them. Holds only the settings that apply to
    all sections at once:

    - ``reasoning_effort`` and ``deployment`` -- both change what the model emits;
    - ``dedup`` -- changes the key scheme (flipping it would otherwise orphan every
      record).

    Per-section concerns (prompt + schema) are fingerprinted in the work-unit key
    instead (see ``SECTION_FINGERPRINTS``), so editing one section invalidates only
    that section rather than the whole checkpoint. Section *selection* affects
    neither, so toggling which sections to parse never invalidates anything.
    """
    payload = f"dedup={dedup}|effort={reasoning_effort}|deployment={deployment}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def load_checkpoint(
    path: Path, expected_sig: str
) -> dict[str, Optional[dict[str, Any]]]:
    """Return ``{key: result_dict_or_None}`` for completed units matching the schema.

    Records written under a different schema signature are skipped (re-queued),
    with a warning, so an interrupted run resumed after a schema edit does not
    emit silently wrong/partial rows.
    """
    done: dict[str, Optional[dict[str, Any]]] = {}
    if not path.exists():
        return done
    stale = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                key = record["key"]
            except (json.JSONDecodeError, KeyError):
                continue  # tolerate a partially-written final line
            if record.get("sig") != expected_sig:
                stale += 1
                done.pop(key, None)
                continue
            done[key] = record.get("result")
    if stale:
        logger.warning(
            "ignored %d checkpoint record(s) from a different schema version; those "
            "units will be re-processed. Use --fresh to start clean.",
            stale,
        )
    return done


class CheckpointWriter:
    """Append-only, lock-guarded JSONL writer for completed work units."""

    def __init__(self, path: Path, sig: str):
        self._lock = asyncio.Lock()
        self._sig = sig
        self._fh = open(path, "a", encoding="utf-8")

    async def write(self, key: str, result: Optional[dict[str, Any]]) -> None:
        async with self._lock:
            self._fh.write(
                json.dumps({"key": key, "sig": self._sig, "result": result}) + "\n"
            )
            self._fh.flush()

    def close(self) -> None:
        self._fh.close()


# Work units ----


class WorkUnit:
    __slots__ = ("key", "schema", "prompt", "text")

    def __init__(self, key: str, schema: type[BaseModel], prompt: str, text: str):
        self.key = key
        self.schema = schema
        self.prompt = prompt
        self.text = text


_KEY_SEP = "\x1f"


def _text_digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def section_fingerprint(schema: type[BaseModel], prompt: str) -> str:
    """Hash of a section's prompt + full JSON schema.

    The full ``model_json_schema()`` (not just field names) is what the structured-
    output call sends to the model, so this captures field descriptions, types,
    enums and constraints as well as names. Folded into the work-unit key so editing
    one section's prompt or schema invalidates only that section's cached cells.

    Note: this is tied to pydantic's schema serialization, so a pydantic upgrade
    that changes ``model_json_schema()`` output would flip every fingerprint and
    force a one-time full reparse (cost, not correctness; ``sort_keys`` already
    absorbs dict-ordering churn).
    """
    schema_json = json.dumps(schema.model_json_schema(), sort_keys=True)
    payload = f"{prompt}{_KEY_SEP}{schema_json}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


# Per-section (prompt + schema) fingerprints, computed once. Live in the key so a
# change to one section reparses only that section. Global params that affect every
# section (reasoning_effort, deployment, dedup) live in schema_signature instead.
SECTION_FINGERPRINTS: dict[str, str] = {
    section: section_fingerprint(schema, prompt)
    for section, (schema, prompt) in INSTANCE_SECTIONS.items()
}


def unit_key(order_id: Any, instance: Any, section: str, text: str, dedup: bool) -> str:
    """Content-addressed checkpoint key for one work unit.

    Every key folds in (a) a hash of the section text and (b) the section's
    prompt+schema fingerprint, so changed source text *or* a changed prompt/schema
    yields a new key (auto-reparsed, since it is not in the checkpoint) while
    everything unchanged keeps the same key (skipped). Both call sites --
    build_work_units and assemble_rows -- go through this single helper so their
    keys cannot drift.

    The model output is a pure function of (section prompt, section schema, section
    text) -- no order_id/instance reaches the model -- so the key scheme is the same
    for every section (no per-section special cases):

    - ``dedup`` (default): key is ``(section, fingerprint, hash)``. Byte-identical
      text anywhere in the file collapses to one unit, parsed once and fanned out.
      Lossless given context-free prompts. This is also what makes a single
      diagnosis repeated across a report's instances cost one call.
    - not ``dedup``: key is ``(order_id, instance, section, fingerprint, hash)``.
      No collapse; each cell is its own unit (change-detection only).
    """
    fp = SECTION_FINGERPRINTS[section]
    digest = _text_digest(text)
    if dedup:
        return f"{section}{_KEY_SEP}{fp}{_KEY_SEP}{digest}"
    return f"{order_id}{_KEY_SEP}{instance}{_KEY_SEP}{section}{_KEY_SEP}{fp}{_KEY_SEP}{digest}"


def build_work_units(
    rows: list[dict[str, Any]], enabled: list[str], dedup: bool
) -> tuple[list[WorkUnit], int, int]:
    """Build the work units for the enabled sections, one per non-empty cell.

    Keys are content-addressed (see ``unit_key``). With ``dedup`` on, byte-identical
    text collapses to a single unit via the shared ``seen`` set; with it off, every
    cell is its own unit. Cells that ``is_empty_cell`` flags (null/blank/placeholder)
    are skipped -- they never become a unit, so they cost no API call.

    Returns ``(units, n_duplicate_cells, n_skipped_empty)``: how many non-empty cells
    were collapsed away by dedup, and how many non-null cells were dropped as
    placeholder/empty (JSON nulls are not counted -- they were never content).
    """
    units: list[WorkUnit] = []
    seen: set[str] = set()
    n_cells = 0
    n_skipped_empty = 0

    instance_sections = selected_instance_sections(enabled)
    for row in rows:
        order_id = row.get("order_id")
        instance = row.get("instance")
        for section, (schema, prompt) in instance_sections.items():
            text = row.get(section)
            if is_empty_cell(text):
                # Count only non-null placeholders ("NA", ".", ...); a JSON null
                # section was never content, so it is not a "skipped" cell.
                if text is not None and str(text).strip():
                    n_skipped_empty += 1
                continue
            text = str(text)
            n_cells += 1
            key = unit_key(order_id, instance, section, text, dedup)
            if key in seen:
                continue
            seen.add(key)
            units.append(WorkUnit(key, schema, prompt, text))

    return units, n_cells - len(units), n_skipped_empty


# Run ----


async def run_unit(
    unit: WorkUnit,
    client: AsyncAzureOpenAI,
    deployment: str,
    limiter: RateLimiter,
    checkpoint: CheckpointWriter,
    results: dict[str, Optional[dict[str, Any]]],
    stats: RunStats,
    max_retries: int,
    retry_base_delay: float,
    reasoning_effort: str,
    pbar: tqdm,
) -> None:
    est = estimate_tokens(unit.prompt, unit.text)
    result: Optional[dict[str, Any]] = None
    # Settled (success/refusal/non-retryable) units are checkpointed so they are
    # not re-queued; a transient exhausted-retry failure stays unset to retry next run.
    checkpoint_it = False

    for attempt in range(max_retries + 1):
        # Hold a concurrency slot only for the call itself; the backoff sleep
        # below runs outside it so a retrying unit doesn't park a scarce slot.
        async with limiter.sem:
            # Re-acquire per attempt so retried calls also count against RPM/TPM.
            await limiter.acquire(est)
            stats.calls += 1
            # In-flight gauge measured here, not from the semaphore (whose occupancy
            # includes tasks parked in acquire). Decrement in finally: the excepts
            # below are inside this block, so a decrement after the await would be
            # skipped on the 429/retry path and leak the gauge past max_concurrency.
            stats.note_inflight_start()
            try:
                outcome = await parse_section(
                    client,
                    deployment,
                    unit.prompt,
                    unit.text,
                    unit.schema,
                    reasoning_effort,
                )
                stats.actual_tokens += outcome.tokens
                result = (
                    outcome.parsed.model_dump() if outcome.parsed is not None else None
                )
                if outcome.refused:
                    stats.refusals += 1
                    logger.debug("[refusal, recorded null] %s", unit.key)
                else:
                    stats.parsed_ok += 1
                checkpoint_it = True
                break
            except LengthFinishReasonError:
                # Deterministic -- retrying repeats it; record a permanent null.
                stats.length_nulls += 1
                logger.debug(
                    "[non-retryable, recorded null] %s: LengthFinishReasonError",
                    unit.key,
                )
                checkpoint_it = True
                break
            except ContentFilterFinishReasonError:
                stats.content_filter_nulls += 1
                logger.debug(
                    "[non-retryable, recorded null] %s: ContentFilterFinishReasonError",
                    unit.key,
                )
                checkpoint_it = True
                break
            except RateLimitError as exc:
                # Server throttling (429). Rare/actionable -> WARNING so occasional
                # throttling is visible even at the default level, not just when it
                # exhausts every retry.
                stats.http_429 += 1
                retry_after = _retry_after(exc)
                if attempt >= max_retries:
                    stats.exhausted_failures += 1
                    logger.error(
                        "[429 exhausted, will retry on rerun] %s (attempt %d/%d)",
                        unit.key,
                        attempt + 1,
                        max_retries + 1,
                    )
                    break
                stats.retries += 1
                logger.warning(
                    "[429, backing off] %s (attempt %d/%d)%s",
                    unit.key,
                    attempt + 1,
                    max_retries + 1,
                    f", Retry-After={retry_after}" if retry_after else "",
                )
            except Exception as exc:  # noqa: BLE001 - continue-on-error by design
                stats.other_retryable += 1
                if attempt >= max_retries:
                    stats.exhausted_failures += 1
                    logger.error(
                        "[failed, will retry on rerun] %s: %s",
                        unit.key,
                        type(exc).__name__,
                    )
                    break
                stats.retries += 1
                logger.debug(
                    "[retryable error] %s: %s (attempt %d/%d)",
                    unit.key,
                    type(exc).__name__,
                    attempt + 1,
                    max_retries + 1,
                )
            finally:
                stats.note_inflight_end()
        # Reached only on a retryable, non-final failure (all other paths break).
        await asyncio.sleep(retry_base_delay * (2**attempt))

    results[unit.key] = result
    if checkpoint_it:
        await checkpoint.write(unit.key, result)
    pbar.update(1)


def _retry_after(exc: RateLimitError) -> Optional[str]:
    """The server's Retry-After header if present (logged, not yet honored)."""
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    try:
        return resp.headers.get("retry-after")
    except Exception:  # noqa: BLE001 - header access is best-effort
        return None


async def process(
    units: list[WorkUnit],
    results: dict[str, Optional[dict[str, Any]]],
    stats: RunStats,
    client: AsyncAzureOpenAI,
    deployment: str,
    limiter: RateLimiter,
    checkpoint: CheckpointWriter,
    max_retries: int,
    retry_base_delay: float,
    reasoning_effort: str,
) -> None:
    with tqdm(total=len(units), desc="Parsing sections", unit="call") as pbar:
        await asyncio.gather(
            *(
                run_unit(
                    unit,
                    client,
                    deployment,
                    limiter,
                    checkpoint,
                    results,
                    stats,
                    max_retries,
                    retry_base_delay,
                    reasoning_effort,
                    pbar,
                )
                for unit in units
            )
        )


# Output assembly ----


def build_fieldnames(enabled: list[str]) -> list[str]:
    fields = list(ID_COLUMNS)
    for section, (schema, _) in selected_instance_sections(enabled).items():
        fields.extend(f"{section}_{name}" for name in schema.model_fields)
    return fields


def assemble_rows(
    rows: list[dict[str, Any]],
    results: dict[str, Optional[dict[str, Any]]],
    enabled: list[str],
    dedup: bool,
) -> list[dict[str, Any]]:
    out_rows: list[dict[str, Any]] = []
    instance_sections = selected_instance_sections(enabled)
    for row in rows:
        order_id = row.get("order_id")
        instance = row.get("instance")
        out: dict[str, Any] = {col: row.get(col) for col in ID_COLUMNS}

        for section in instance_sections:
            # Same empty-cell guard as build_work_units (shared is_empty_cell) so the
            # key matches the one the unit was stored under -- a drift here would
            # silently miss lookups, and placeholder cells stay blank in the output.
            text = row.get(section)
            if is_empty_cell(text):
                continue
            parsed = results.get(unit_key(order_id, instance, section, str(text), dedup))
            if parsed:
                for name, value in parsed.items():
                    out[f"{section}_{name}"] = value

        out_rows.append(out)
    return out_rows


def write_csv(
    out_rows: list[dict[str, Any]], output_path: Path, enabled: list[str]
) -> None:
    fieldnames = build_fieldnames(enabled)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # csv writes None as an empty string -> null fields render as blank cells.
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(out_rows)


# Entry point ----


def read_jsonl(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    bad = 0
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                bad += 1
                logger.warning("skipping malformed JSON on line %d: %s", lineno, exc)
    if bad:
        logger.warning("skipped %d malformed line(s) in %s", bad, path)
    return rows


def check_id_columns(rows: list[dict[str, Any]]) -> None:
    """Verify the passthrough identity columns are present in the input.

    Presence is judged over the whole file: a column counts as present if any row
    carries it, so a stray row missing an optional key does not trip the guard.
    A missing ``instance`` (optional) is a single warning; any missing mandatory
    column aborts before we spend a cent on the model.
    """
    if not rows:
        return
    present = set().union(*(row.keys() for row in rows))
    missing = [col for col in ID_COLUMNS if col not in present]
    missing_required = [col for col in missing if col not in OPTIONAL_ID_COLUMNS]
    missing_optional = [col for col in missing if col in OPTIONAL_ID_COLUMNS]

    for col in missing_optional:
        logger.warning(
            "optional column '%s' is absent from the input; it will be emitted "
            "empty. Continuing.",
            col,
        )
    if missing_required:
        cols = ", ".join(f"'{c}'" for c in missing_required)
        raise SystemExit(
            "ERROR: the input is missing mandatory identity column(s): "
            f"{cols}. These are carried verbatim into every output row, so parsing "
            "cannot proceed without them. Check that the input column headers match "
            f"the expected names ({', '.join(ID_COLUMNS)}) and re-run."
        )


async def async_main(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    az = config["azure_openai"]
    proc = config["processing"]
    files = config["files"]
    sections = config["sections"]

    # CLI wins over config: --quiet -> ERROR, then --log-level, else config default.
    log_level = "ERROR" if args.quiet else (args.log_level or proc["log_level"])
    setup_logging(log_level, args.log_file)

    concurrency = (
        args.concurrency if args.concurrency is not None else proc["max_concurrency"]
    )
    if concurrency < 1:
        raise SystemExit("--concurrency must be >= 1")

    input_path = files["input_jsonl"]
    print(f"Reading input: {input_path}")
    rows = read_jsonl(input_path)
    if args.limit is not None:
        if args.limit < 0:
            raise SystemExit("--limit must be >= 0")
        rows = rows[: args.limit]
        print(f"--limit: using first {len(rows)} rows")

    check_id_columns(rows)

    dedup = proc["dedup"]
    units, n_duplicates, n_skipped_empty = build_work_units(rows, sections, dedup)

    checkpoint_path = Path(files["checkpoint"])
    if args.fresh and checkpoint_path.exists():
        checkpoint_path.unlink()
        print("--fresh: removed existing checkpoint")

    sig = schema_signature(dedup, az["reasoning_effort"], az["deployment"])
    done = load_checkpoint(checkpoint_path, sig)
    results: dict[str, Optional[dict[str, Any]]] = dict(done)
    pending = [u for u in units if u.key not in done]

    n_cells = len(units) + n_duplicates
    print(f"\n{'=' * 56}")
    print("Processing summary")
    print(f"{'=' * 56}")
    print(f"Input rows (instances):  {len(rows)}")
    if dedup:
        pct = (n_duplicates / n_cells * 100) if n_cells else 0.0
        print(f"Non-empty cells:         {n_cells}")
        print(f"Duplicate cells merged:  {n_duplicates} ({pct:.1f}%)")
    print(f"Skipped empty/NA cells:  {n_skipped_empty}")
    print(f"Total work units:        {len(units)}")
    print(f"Already done (skipped):  {len(units) - len(pending)}")
    print(f"To process now:          {len(pending)}")
    print(f"Sections:                {', '.join(sections)}")
    print(f"Dedup:                   {'on (global)' if dedup else 'off'}")
    print(f"Model / deployment:      {az['deployment']}")
    print(f"Reasoning effort:        {az['reasoning_effort']}")
    print(f"Concurrency:             {concurrency}")
    print(f"{'=' * 56}")

    if not pending:
        print("Nothing to process. Assembling CSV from checkpoint...")
    elif not args.yes:
        if not sys.stdin.isatty():
            print(
                "Non-interactive session and --yes not set; aborting without "
                "processing. Re-run with --yes to proceed."
            )
            return
        try:
            answer = input("\nProceed? (y/n): ").strip().lower()
        except EOFError:
            answer = "n"
        if answer != "y":
            print("Cancelled.")
            return

    if pending:
        client = build_client(az)
        limiter = RateLimiter(concurrency, proc["target_rpm"], proc["target_tpm"])
        checkpoint_writer = CheckpointWriter(checkpoint_path, sig)
        stats = RunStats()
        start = time.monotonic()
        try:
            await process(
                pending,
                results,
                stats,
                client,
                az["deployment"],
                limiter,
                checkpoint_writer,
                proc["max_retries"],
                proc["retry_base_delay"],
                az["reasoning_effort"],
            )
        finally:
            checkpoint_writer.close()
            await client.close()
        # End-of-run report: outcomes, throughput, and which limit is binding.
        # Printed (not logged) so it always shows regardless of console log level.
        print(
            "\n"
            + format_run_report(
                stats,
                limiter,
                time.monotonic() - start,
                concurrency,
                proc["target_rpm"],
                proc["target_tpm"],
                n_skipped_empty,
            )
        )

    out_rows = assemble_rows(rows, results, sections, dedup)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = (
        Path(files["output_dir"]) / f"{files['output_prefix']}_{timestamp}.csv"
    )
    write_csv(out_rows, output_path, sections)

    print(f"\nWrote {len(out_rows)} rows to: {output_path}")
    print(f"Checkpoint retained at: {checkpoint_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse bone marrow report sections.")
    default_config = str(Path(__file__).with_name("config.yaml"))
    parser.add_argument("--config", default=default_config, help="Path to config.yaml")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process first N input rows (smoke test)",
    )
    parser.add_argument(
        "--fresh", action="store_true", help="Ignore/remove existing checkpoint"
    )
    parser.add_argument(
        "--concurrency", type=int, default=None, help="Override max concurrency"
    )
    parser.add_argument(
        "--yes", action="store_true", help="Skip the confirmation prompt"
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=None,
        help="Console log level (overrides config; default WARNING)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only log errors (shortcut for --log-level ERROR)",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Also append metadata-only logs (no report text) to this file",
    )
    args = parser.parse_args()

    try:
        asyncio.run(async_main(args))
    except FileNotFoundError as exc:
        print(f"Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
