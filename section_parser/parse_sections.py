"""Parse bone marrow report sections via Azure OpenAI structured outputs into one flat CSV.

Usage:
    python -m section_parser.parse_sections [--config PATH] [--limit N]
                                            [--fresh] [--concurrency N] [--yes]
"""

import argparse
import asyncio
import csv
import hashlib
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from azure.identity import (
    AzureCliCredential,
    InteractiveBrowserCredential,
    get_bearer_token_provider,
)
from openai import (
    AsyncAzureOpenAI,
    AuthenticationError,
    BadRequestError,
    ContentFilterFinishReasonError,
    LengthFinishReasonError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import BaseModel
from tqdm import tqdm

from section_parser.runlog import (
    RunStats,
    format_run_report,
    get_logger,
    setup_logging,
)
from section_parser.schemas import SECTIONS as INSTANCE_SECTIONS

logger = get_logger()

ALL_SECTIONS: list[str] = list(INSTANCE_SECTIONS)

# Placeholder tokens treated as empty
EMPTY_SENTINELS = {"na", "n/a", "none", "nil", "null", "-", "--", "."}


def is_empty_cell(value: Any) -> bool:
    """True if a cell carries no parseable content (null/blank/placeholder)."""
    if value is None:
        return True
    stripped = str(value).strip()
    return not stripped or stripped.lower() in EMPTY_SENTINELS


def selected_instance_sections(
    enabled: list[str],
) -> dict[str, tuple[type[BaseModel], str]]:
    """Instance-level sections to parse, preserving canonical order."""
    return {k: v for k, v in INSTANCE_SECTIONS.items() if k in enabled}


def _is_placeholder(value: Any) -> bool:
    """A "<" anywhere catches half-edited values."""
    return not value or (isinstance(value, str) and "<" in value)


def load_config(config_path: str) -> dict[str, Any]:
    """Load the YAML config."""
    cfg = Path(config_path)
    if not cfg.exists():
        example = cfg.parent / f"{cfg.name}.example"
        raise SystemExit(
            f"Config file not found at '{config_path}'. Copy '{example.name}' to '{cfg.name}' and fill in real values."
        )
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    az = config["azure_openai"]

    az.setdefault("auth", "cli")
    if az["auth"] not in {"cli", "browser"}:
        raise SystemExit(f"Azure config 'auth' must be 'cli' or 'browser'; got {az['auth']!r}.")

    # tenant_id is only required for browser auth; CLI auth derives it from `az login`.
    required = ["endpoint", "deployment"]
    if az["auth"] == "browser":
        required.append("tenant_id")
    for field in required:
        if _is_placeholder(az.get(field, "")):
            raise SystemExit(
                f"Azure config '{field}' is unset/placeholder in '{cfg.name}'. "
                "Fill in your real value (see config.yaml.example)."
            )

    az.setdefault("reasoning_effort", "low")
    valid_efforts = {"none", "minimal", "low", "medium", "high", "xhigh"}
    if az["reasoning_effort"] not in valid_efforts:
        raise SystemExit(
            f"Azure config 'reasoning_effort' must be one of {sorted(valid_efforts)}; got {az['reasoning_effort']!r}."
        )

    # Required -- also doubles as the set of mandatory input columns (see check_input_columns).
    sections = config.get("sections")
    if sections is None:
        raise SystemExit(f"Config 'sections' is required; list the section(s) to parse (valid: {ALL_SECTIONS}).")
    if not isinstance(sections, list):
        raise SystemExit("Config 'sections' must be a list of section names.")
    unknown = [s for s in sections if s not in ALL_SECTIONS]
    if unknown:
        raise SystemExit(f"Config 'sections' has unknown name(s) {unknown}; valid sections are {ALL_SECTIONS}.")
    selected = [s for s in ALL_SECTIONS if s in sections]
    if not selected:
        raise SystemExit("Config 'sections' is empty; list at least one section.")
    config["sections"] = selected

    files = config.get("files")
    if files is None:
        files = {}
    elif not isinstance(files, dict):
        raise SystemExit("Config 'files' must be a mapping of settings.")
    config["files"] = files
    # Lets a renamed identity column (e.g. order_id -> accession_number) work without
    # touching the parsing logic; both still flow through as ordinary passthrough columns.
    files.setdefault("order_id_col", "order_id")
    files.setdefault("instance_col", "instance")

    # Validated because 0 wouldn't error, it'd hang silently (empty semaphore / always-false rate check).
    proc = config.get("processing")
    if proc is None:
        proc = {}
    elif not isinstance(proc, dict):
        raise SystemExit("Config 'processing' must be a mapping of settings, not a list/scalar.")
    config["processing"] = proc
    proc.setdefault("max_concurrency", 5)
    proc.setdefault("max_retries", 5)
    proc.setdefault("retry_base_delay", 2.0)

    v = proc["max_concurrency"]
    # bool is an int subclass in Python, so exclude it or `true` would silently pass as 1.
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise SystemExit(f"Config 'processing.max_concurrency' must be a positive integer; got {v!r}.")

    v = proc["max_retries"]
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise SystemExit(f"Config 'processing.max_retries' must be a non-negative integer; got {v!r}.")

    v = proc["retry_base_delay"]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        raise SystemExit(f"Config 'processing.retry_base_delay' must be a non-negative number; got {v!r}.")

    proc.setdefault("dedup", True)
    if not isinstance(proc["dedup"], bool):
        raise SystemExit(f"Config 'processing.dedup' must be true or false; got {proc['dedup']!r}.")

    proc.setdefault("log_level", "WARNING")
    level = str(proc["log_level"]).upper()
    valid_levels = {"DEBUG", "WARNING"}
    if level not in valid_levels:
        raise SystemExit(
            f"Config 'processing.log_level' must be one of {sorted(valid_levels)}; got {proc['log_level']!r}."
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

    # Fetch a token up front so a missing `az login` fails fast, not deep inside run_unit's retry loop.
    try:
        credential.get_token(az["scope"])
    except Exception as exc:  # noqa: BLE001
        hint = (
            "Run `az login` (and `az account set --subscription ...` if needed)."
            if az["auth"] == "cli"
            else "Complete the browser sign-in when prompted."
        )
        raise SystemExit(f"Azure authentication failed ({type(exc).__name__}): {exc}\n{hint}") from None

    token_provider = get_bearer_token_provider(credential, az["scope"])
    return AsyncAzureOpenAI(
        azure_endpoint=az["endpoint"],
        azure_ad_token_provider=token_provider,
        api_version=az["api_version"],
        max_retries=0,  # run_unit owns retry/backoff; stacking the SDK's own retries caused multi-minute hangs
    )


@dataclass
class CallOutcome:
    """Result of one model call: parsed value, whether it was a refusal, and actual token usage."""

    parsed: BaseModel | None
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
    """Run one structured-output call."""
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


def schema_signature(dedup: bool, reasoning_effort: str, deployment: str) -> str:
    """Checkpoint fingerprint of run-wide settings; a mismatch purges stale records on load."""
    payload = f"dedup={dedup}|effort={reasoning_effort}|deployment={deployment}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def load_checkpoint(path: Path, expected_sig: str) -> dict[str, dict[str, Any] | None]:
    """Return ``{key: result_dict_or_None}`` for completed units matching the schema signature."""
    done: dict[str, dict[str, Any] | None] = {}
    if not path.exists():
        return done
    stale = 0
    with open(path, encoding="utf-8") as f:
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
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(path, "a", encoding="utf-8")

    async def write(self, key: str, result: dict[str, Any] | None) -> None:
        async with self._lock:
            self._fh.write(json.dumps({"key": key, "sig": self._sig, "result": result}) + "\n")
            self._fh.flush()

    def close(self) -> None:
        self._fh.close()


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
    """Hash of a section's prompt + full JSON schema, so editing either invalidates only that section's cache."""
    schema_json = json.dumps(schema.model_json_schema(), sort_keys=True)
    payload = f"{prompt}{_KEY_SEP}{schema_json}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


SECTION_FINGERPRINTS: dict[str, str] = {
    section: section_fingerprint(schema, prompt) for section, (schema, prompt) in INSTANCE_SECTIONS.items()
}


def unit_key(order_id: Any, instance: Any, section: str, text: str, dedup: bool) -> str:
    """Content-addressed checkpoint key; with dedup, byte-identical text anywhere collapses to one unit."""
    fp = SECTION_FINGERPRINTS[section]
    digest = _text_digest(text)
    if dedup:
        return f"{section}{_KEY_SEP}{fp}{_KEY_SEP}{digest}"
    return f"{order_id}{_KEY_SEP}{instance}{_KEY_SEP}{section}{_KEY_SEP}{fp}{_KEY_SEP}{digest}"


def build_work_units(
    rows: list[dict[str, Any]],
    enabled: list[str],
    dedup: bool,
    order_id_col: str,
    instance_col: str,
) -> tuple[list[WorkUnit], int, int]:
    """Build one work unit per non-empty cell; returns (units, n_duplicate_cells, n_skipped_empty)."""
    units: list[WorkUnit] = []
    seen: set[str] = set()
    n_cells = 0
    n_skipped_empty = 0

    instance_sections = selected_instance_sections(enabled)
    for row in rows:
        order_id = row.get(order_id_col)
        instance = row.get(instance_col)
        for section, (schema, prompt) in instance_sections.items():
            text = row.get(section)
            if is_empty_cell(text):
                # placeholders like "NA" count as skipped; JSON nulls don't
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


async def run_unit(
    unit: WorkUnit,
    client: AsyncAzureOpenAI,
    deployment: str,
    sem: asyncio.Semaphore,
    checkpoint: CheckpointWriter,
    results: dict[str, dict[str, Any] | None],
    stats: RunStats,
    max_retries: int,
    retry_base_delay: float,
    reasoning_effort: str,
    pbar: tqdm,
) -> None:
    result: dict[str, Any] | None = None
    checkpoint_it = False  # False on exhausted retries means re-queued next run
    server_delay: float | None = None  # Retry-After from the last 429, if any

    for attempt in range(max_retries + 1):
        async with sem:  # the backoff sleep below runs outside the slot
            stats.calls += 1
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
                result = outcome.parsed.model_dump() if outcome.parsed is not None else None
                if outcome.refused:
                    stats.refusals += 1
                    logger.debug("[refusal, recorded null] %s", unit.key)
                else:
                    stats.parsed_ok += 1
                checkpoint_it = True
                break
            except LengthFinishReasonError:  # deterministic -- record a permanent null
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
            except (BadRequestError, AuthenticationError, PermissionDeniedError, NotFoundError) as exc:
                # Azure returns a prompt-side content filter as a 400, not a finish reason.
                if getattr(exc, "code", None) == "content_filter":
                    stats.content_filter_nulls += 1
                    logger.debug("[prompt content filter, recorded null] %s", unit.key)
                    checkpoint_it = True
                    break
                # Bad endpoint/api_version/deployment or missing role: retrying only hides it.
                # Not checkpointed, so a re-run retries once the config is fixed.
                stats.fatal_api_errors += 1
                status = getattr(exc, "status_code", "?")
                if stats.first_seen(f"fatal:{status}"):
                    logger.error("[HTTP %s, not retryable -- fix and re-run] %s: %s", status, unit.key, exc)
                else:
                    logger.debug("[HTTP %s, not retryable] %s", status, unit.key)
                break
            except RateLimitError as exc:
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
                server_delay = retry_after
                logger.warning(
                    "[429, backing off] %s (attempt %d/%d)%s",
                    unit.key,
                    attempt + 1,
                    max_retries + 1,
                    f", Retry-After={retry_after:.1f}s" if retry_after is not None else "",
                )
            except Exception as exc:  # noqa: BLE001
                stats.other_retryable += 1
                # One line per error kind, so a systematic failure isn't silent at WARNING.
                if stats.first_seen(f"retryable:{type(exc).__name__}"):
                    logger.warning("[retryable error, backing off] %s: %s", unit.key, exc)
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
        # only reached on a retryable, non-final failure; the server's own figure
        # wins over the backoff curve when a 429 supplied one.
        backoff = retry_base_delay * (2**attempt)
        await asyncio.sleep(max(server_delay, backoff) if server_delay is not None else backoff)
        server_delay = None

    results[unit.key] = result
    if checkpoint_it:
        await checkpoint.write(unit.key, result)
    pbar.update(1)


def _retry_after(exc: RateLimitError) -> float | None:
    """The server's requested retry delay in seconds, if it sent one."""
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    try:
        headers = resp.headers
    except Exception:  # noqa: BLE001
        return None
    for name, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
        raw = headers.get(name)
        if raw is not None:
            try:
                return float(raw) * scale
            except (TypeError, ValueError):
                return None
    return None


async def process(
    units: list[WorkUnit],
    results: dict[str, dict[str, Any] | None],
    stats: RunStats,
    client: AsyncAzureOpenAI,
    deployment: str,
    sem: asyncio.Semaphore,
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
                    sem,
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


def passthrough_columns(rows: list[dict[str, Any]]) -> list[str]:
    """Every input key that isn't a known section, in first-seen order; carried verbatim to the CSV."""
    cols: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key in ALL_SECTIONS or key in seen:
                continue
            seen.add(key)
            cols.append(key)
    return cols


def build_fieldnames(passthrough: list[str], enabled: list[str]) -> list[str]:
    fields = list(passthrough)
    for section, (schema, _) in selected_instance_sections(enabled).items():
        fields.extend(f"{section}_{name}" for name in schema.model_fields)
    return fields


def assemble_rows(
    rows: list[dict[str, Any]],
    results: dict[str, dict[str, Any] | None],
    enabled: list[str],
    dedup: bool,
    passthrough: list[str],
    order_id_col: str,
    instance_col: str,
) -> list[dict[str, Any]]:
    out_rows: list[dict[str, Any]] = []
    instance_sections = selected_instance_sections(enabled)
    for row in rows:
        order_id = row.get(order_id_col)
        instance = row.get(instance_col)
        out: dict[str, Any] = {col: row.get(col) for col in passthrough}

        for section in instance_sections:
            # must match build_work_units' empty-cell guard or the lookup key drifts
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
    out_rows: list[dict[str, Any]],
    output_path: Path,
    enabled: list[str],
    passthrough: list[str],
) -> None:
    fieldnames = build_fieldnames(passthrough, enabled)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(out_rows)


def read_jsonl(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    bad = 0
    with open(path, encoding="utf-8") as f:
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


def check_input_columns(rows: list[dict[str, Any]], enabled: list[str], order_id_col: str, instance_col: str) -> None:
    """Verify the enabled section columns are present; everything else is optional passthrough."""
    if not rows:
        return
    present = set().union(*(row.keys() for row in rows))
    missing = [s for s in enabled if s not in present]
    if missing:
        cols = ", ".join(f"'{c}'" for c in missing)
        raise SystemExit(
            f"ERROR: the input is missing section column(s) enabled in config: {cols}. "
            "Check that the input column headers match the enabled sections "
            f"({', '.join(enabled)}) and re-run."
        )

    # instance is only load-bearing when order_id repeats -- that's the one
    # case where rows become genuinely ambiguous without it.
    if instance_col not in present:
        dupes = [oid for oid, n in Counter(row.get(order_id_col) for row in rows).items() if n > 1]
        if dupes:
            logger.warning(
                "column '%s' is absent but %d '%s' value(s) repeat across rows; "
                "those rows can't be told apart without it.",
                instance_col,
                len(dupes),
                order_id_col,
            )


async def async_main(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    az = config["azure_openai"]
    proc = config["processing"]
    files = config["files"]
    sections = config["sections"]

    log_level = args.log_level or proc["log_level"]  # CLI overrides config default
    setup_logging(log_level, args.log_file)

    concurrency = args.concurrency if args.concurrency is not None else proc["max_concurrency"]
    if concurrency < 1:
        raise SystemExit("--concurrency must be >= 1")

    order_id_col = files["order_id_col"]
    instance_col = files["instance_col"]

    input_path = files["input_jsonl"]
    print(f"Reading input: {input_path}")
    rows = read_jsonl(input_path)
    if args.limit is not None:
        if args.limit < 0:
            raise SystemExit("--limit must be >= 0")
        rows = rows[: args.limit]
        print(f"--limit: using first {len(rows)} rows")

    check_input_columns(rows, sections, order_id_col, instance_col)
    passthrough = passthrough_columns(rows)

    dedup = proc["dedup"]
    units, n_duplicates, n_skipped_empty = build_work_units(rows, sections, dedup, order_id_col, instance_col)

    checkpoint_path = Path(files["checkpoint"])
    if args.fresh and checkpoint_path.exists():
        checkpoint_path.unlink()
        print("--fresh: removed existing checkpoint")

    sig = schema_signature(dedup, az["reasoning_effort"], az["deployment"])
    done = load_checkpoint(checkpoint_path, sig)
    results: dict[str, dict[str, Any] | None] = dict(done)
    pending = [u for u in units if u.key not in done]

    n_cells = len(units) + n_duplicates
    print(f"\n{'=' * 56}")
    print("Processing summary")
    print(f"{'=' * 56}")
    print(f"Input rows (instances):  {len(rows)}")
    print(f"Dedup:                   {'on (global)' if dedup else 'off'}")
    if dedup:
        pct = (n_duplicates / n_cells * 100) if n_cells else 0.0
        print(f"Non-empty cells:         {n_cells}")
        print(f"Duplicate cells merged:  {n_duplicates} ({pct:.1f}%)")
    print(f"Skipped empty/NA cells:  {n_skipped_empty}")
    print(f"Total work units:        {len(units)}")
    print(f"Already done (skipped):  {len(units) - len(pending)}")
    print(f"To process now:          {len(pending)}")
    print(f"Sections:                {', '.join(sections)}")
    print(f"Model / deployment:      {az['deployment']}")
    print(f"Reasoning effort:        {az['reasoning_effort']}")
    print(f"Concurrency:             {concurrency}")
    print(f"{'=' * 56}")

    if not pending:
        print("Nothing to process. Assembling CSV from checkpoint...")
    elif not args.yes:
        if not sys.stdin.isatty():
            print(
                "Non-interactive session and --yes not set; aborting without processing. Re-run with --yes to proceed."
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
        sem = asyncio.Semaphore(concurrency)
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
                sem,
                checkpoint_writer,
                proc["max_retries"],
                proc["retry_base_delay"],
                az["reasoning_effort"],
            )
        finally:
            checkpoint_writer.close()
            await client.close()
        print(  # printed, not logged, so it always shows regardless of console log level
            "\n"
            + format_run_report(
                stats,
                time.monotonic() - start,
                concurrency,
                n_skipped_empty,
            )
        )

    out_rows = assemble_rows(rows, results, sections, dedup, passthrough, order_id_col, instance_col)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(files["output_dir"]) / f"{files['output_prefix']}_{timestamp}.csv"
    write_csv(out_rows, output_path, sections, passthrough)

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
    parser.add_argument("--fresh", action="store_true", help="Ignore/remove existing checkpoint")
    parser.add_argument("--concurrency", type=int, default=None, help="Override max concurrency")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "WARNING"],
        default=None,
        help="Console log level: DEBUG for tuning concurrency/rate limits, "
        "WARNING for a quiet run (overrides config; default WARNING)",
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
