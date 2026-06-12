"""Parse bone marrow report sections via Azure OpenAI structured outputs.

Reads ``data/sections.jsonl`` (one specimen instance per line), parses each
non-null section with its own schema + system prompt, and writes one flat,
timestamped CSV (one row per ``(order_id, instance)``).

Auth is interactive browser-based Azure AD (no API key). Calls run concurrently
with gentle rate limiting, and progress is checkpointed so an interrupted run
can resume.

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
import os
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml
from azure.identity import InteractiveBrowserCredential, get_bearer_token_provider
from openai import (
    AsyncAzureOpenAI,
    ContentFilterFinishReasonError,
    LengthFinishReasonError,
)
from pydantic import BaseModel
from tqdm import tqdm

from section_parser import prompts
from section_parser.schemas import (
    AspirateSchema,
    BiopsySchema,
    CellCountSchema,
    FinalDxSchema,
    FlowSchema,
    ImmunostainsSchema,
)

# ---------------------------------------------------------------------------
# Section configuration
# ---------------------------------------------------------------------------

# Order here determines column order in the output CSV.
INSTANCE_SECTIONS: dict[str, tuple[type[BaseModel], str]] = {
    "biopsy": (BiopsySchema, prompts.BIOPSY_PROMPT),
    "aspirate": (AspirateSchema, prompts.ASPIRATE_PROMPT),
    "flow": (FlowSchema, prompts.FLOW_PROMPT),
    "cell_count": (CellCountSchema, prompts.CELL_COUNT_PROMPT),
    "immunostains": (ImmunostainsSchema, prompts.IMMUNOSTAINS_PROMPT),
}

# Report-level section, deduped per order_id.
FINAL_DX_SECTION = (FinalDxSchema, prompts.FINAL_DX_PROMPT)
FINAL_DX_KEY = "final_dx"

ID_COLUMNS = ["order_id", "pat_mrn_id", "description", "specimen_date", "instance"]


# ---------------------------------------------------------------------------
# Config & client
# ---------------------------------------------------------------------------


def load_config(config_path: str) -> dict[str, Any]:
    """Load YAML config and apply environment-variable overrides for secrets."""
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    az = config["azure_openai"]
    # Env vars take precedence; fall back to config; reject leftover placeholders.
    az["endpoint"] = os.environ.get("AZURE_OPENAI_ENDPOINT", az.get("endpoint", ""))
    az["deployment"] = os.environ.get("AZURE_OPENAI_DEPLOYMENT", az.get("deployment", ""))
    az["tenant_id"] = os.environ.get("AZURE_TENANT_ID", az.get("tenant_id", ""))

    for field in ("endpoint", "deployment", "tenant_id"):
        value = az.get(field, "")
        if not value or value.startswith("<"):
            raise SystemExit(
                f"Azure config '{field}' is unset/placeholder. Set it in the config "
                f"or via the matching environment variable "
                f"(AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_DEPLOYMENT / AZURE_TENANT_ID)."
            )
    return config


def build_client(az: dict[str, Any]) -> AsyncAzureOpenAI:
    """Build the async client with interactive browser AD auth.

    Credential is created once (single browser prompt); the token provider
    caches and refreshes across the run.
    """
    credential = InteractiveBrowserCredential(tenant_id=az["tenant_id"])
    token_provider = get_bearer_token_provider(credential, az["scope"])
    return AsyncAzureOpenAI(
        azure_endpoint=az["endpoint"],
        azure_ad_token_provider=token_provider,
        api_version=az["api_version"],
    )


# ---------------------------------------------------------------------------
# The single, isolated model call
# ---------------------------------------------------------------------------


async def parse_section(
    client: AsyncAzureOpenAI,
    deployment: str,
    system_prompt: str,
    text: str,
    schema: type[BaseModel],
) -> Optional[BaseModel]:
    """Run one structured-output call -- the ONLY place that touches the model API.

    Reasoning-class model: no ``temperature``/``max_tokens`` (cap with
    ``max_completion_tokens`` if needed). ``chat.completions.parse`` is the
    structured-output helper, promoted out of ``beta`` in openai 2.x.
    """
    completion = await client.chat.completions.parse(
        model=deployment,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        response_format=schema,
    )
    message = completion.choices[0].message
    if getattr(message, "refusal", None):
        return None
    return message.parsed


# ---------------------------------------------------------------------------
# Gentle rate limiting
# ---------------------------------------------------------------------------


class RateLimiter:
    """Bounds concurrency and paces requests/tokens against sliding 60s windows."""

    def __init__(self, max_concurrency: int, target_rpm: int, target_tpm: int):
        self.sem = asyncio.Semaphore(max_concurrency)
        self.target_rpm = target_rpm
        self.target_tpm = target_tpm
        self._requests: deque[float] = deque()
        self._tokens: deque[tuple[float, int]] = deque()
        self._lock = asyncio.Lock()

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
        while True:
            async with self._lock:
                now = time.monotonic()
                self._purge(now)
                tok_sum = sum(t for _, t in self._tokens)
                if (
                    len(self._requests) < self.target_rpm
                    and tok_sum + est_tokens <= self.target_tpm
                ):
                    self._requests.append(now)
                    self._tokens.append((now, est_tokens))
                    return
            await asyncio.sleep(0.5)


def estimate_tokens(system_prompt: str, text: str) -> int:
    """Rough token estimate (~chars/4) + headroom; gpt-5.4 has no public tokenizer.

    Overestimating is intentional -- it keeps us under the TPM ceiling.
    """
    return (len(system_prompt) + len(text)) // 4 + 800


# ---------------------------------------------------------------------------
# Checkpointing
# ---------------------------------------------------------------------------


def schema_signature() -> str:
    """Short fingerprint of the current output schema (all CSV columns).

    Stored on every checkpoint record so a schema change automatically
    invalidates stale entries instead of silently producing mixed-schema output.
    """
    return hashlib.sha1("|".join(build_fieldnames()).encode("utf-8")).hexdigest()[:12]


def load_checkpoint(path: Path, expected_sig: str) -> dict[str, Optional[dict[str, Any]]]:
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
        print(
            f"WARNING: ignored {stale} checkpoint record(s) from a different schema "
            f"version; those units will be re-processed. Use --fresh to start clean."
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


# ---------------------------------------------------------------------------
# Work units
# ---------------------------------------------------------------------------


class WorkUnit:
    __slots__ = ("key", "schema", "prompt", "text")

    def __init__(self, key: str, schema: type[BaseModel], prompt: str, text: str):
        self.key = key
        self.schema = schema
        self.prompt = prompt
        self.text = text


# Unit-separator char can't appear in an order_id, so keys never collide (unlike '|').
_KEY_SEP = "\x1f"


def instance_key(order_id: str, instance: Any, section: str) -> str:
    return f"{order_id}{_KEY_SEP}{instance}{_KEY_SEP}{section}"


def final_dx_key(order_id: str) -> str:
    return f"{order_id}{_KEY_SEP}{FINAL_DX_KEY}"


def build_work_units(rows: list[dict[str, Any]]) -> list[WorkUnit]:
    """Build instance-level units (per non-null section) + one final_dx unit per order_id."""
    units: list[WorkUnit] = []

    for row in rows:
        order_id = row.get("order_id")
        instance = row.get("instance")
        for section, (schema, prompt) in INSTANCE_SECTIONS.items():
            text = row.get(section)
            if text and str(text).strip():
                units.append(
                    WorkUnit(instance_key(order_id, instance, section), schema, prompt, str(text))
                )

    # final_dx: one unit per unique order_id that has non-null diagnosis text.
    seen: set[str] = set()
    schema, prompt = FINAL_DX_SECTION
    for row in rows:
        order_id = row.get("order_id")
        if order_id in seen:
            continue
        text = row.get(FINAL_DX_KEY)
        if text and str(text).strip():
            seen.add(order_id)
            units.append(WorkUnit(final_dx_key(order_id), schema, prompt, str(text)))

    return units


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


async def run_unit(
    unit: WorkUnit,
    client: AsyncAzureOpenAI,
    deployment: str,
    limiter: RateLimiter,
    checkpoint: CheckpointWriter,
    results: dict[str, Optional[dict[str, Any]]],
    max_retries: int,
    retry_base_delay: float,
    pbar: tqdm,
) -> None:
    est = estimate_tokens(unit.prompt, unit.text)
    result: Optional[dict[str, Any]] = None
    # Settled (success/refusal/non-retryable) units are checkpointed so they are
    # not re-queued; a transient exhausted-retry failure stays unset to retry next run.
    checkpoint_it = False

    async with limiter.sem:
        for attempt in range(max_retries + 1):
            # Re-acquire per attempt so retried calls also count against RPM/TPM.
            await limiter.acquire(est)
            try:
                parsed = await parse_section(
                    client, deployment, unit.prompt, unit.text, unit.schema
                )
                result = parsed.model_dump() if parsed is not None else None
                checkpoint_it = True
                break
            except (LengthFinishReasonError, ContentFilterFinishReasonError) as exc:
                # Deterministic -- retrying repeats it; record a permanent null.
                tqdm.write(f"[non-retryable, recorded as null] {unit.key}: {type(exc).__name__}")
                checkpoint_it = True
                break
            except Exception as exc:  # noqa: BLE001 - continue-on-error by design
                if attempt >= max_retries:
                    tqdm.write(f"[failed, will retry on rerun] {unit.key}: {exc}")
                    break
                await asyncio.sleep(retry_base_delay * (2**attempt))

    results[unit.key] = result
    if checkpoint_it:
        await checkpoint.write(unit.key, result)
    pbar.update(1)


async def process(
    units: list[WorkUnit],
    results: dict[str, Optional[dict[str, Any]]],
    client: AsyncAzureOpenAI,
    deployment: str,
    limiter: RateLimiter,
    checkpoint: CheckpointWriter,
    max_retries: int,
    retry_base_delay: float,
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
                    max_retries,
                    retry_base_delay,
                    pbar,
                )
                for unit in units
            )
        )


# ---------------------------------------------------------------------------
# Output assembly
# ---------------------------------------------------------------------------


def build_fieldnames() -> list[str]:
    fields = list(ID_COLUMNS)
    for section, (schema, _) in INSTANCE_SECTIONS.items():
        fields.extend(f"{section}_{name}" for name in schema.model_fields)
    fields.append(f"{FINAL_DX_KEY}_category")
    return fields


def assemble_rows(
    rows: list[dict[str, Any]],
    results: dict[str, Optional[dict[str, Any]]],
) -> list[dict[str, Any]]:
    out_rows: list[dict[str, Any]] = []
    for row in rows:
        order_id = row.get("order_id")
        instance = row.get("instance")
        out: dict[str, Any] = {col: row.get(col) for col in ID_COLUMNS}

        for section in INSTANCE_SECTIONS:
            parsed = results.get(instance_key(order_id, instance, section))
            if parsed:
                for name, value in parsed.items():
                    out[f"{section}_{name}"] = value

        final = results.get(final_dx_key(order_id))
        if final:
            out[f"{FINAL_DX_KEY}_category"] = final.get("category")

        out_rows.append(out)
    return out_rows


def write_csv(out_rows: list[dict[str, Any]], output_path: Path) -> None:
    fieldnames = build_fieldnames()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # csv writes None as an empty string -> null fields render as blank cells.
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(out_rows)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


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
                print(f"WARNING: skipping malformed JSON on line {lineno}: {exc}")
    if bad:
        print(f"WARNING: skipped {bad} malformed line(s) in {path}")
    return rows


async def async_main(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    az = config["azure_openai"]
    proc = config["processing"]
    files = config["files"]

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

    units = build_work_units(rows)

    checkpoint_path = Path(files["checkpoint"])
    if args.fresh and checkpoint_path.exists():
        checkpoint_path.unlink()
        print("--fresh: removed existing checkpoint")

    sig = schema_signature()
    done = load_checkpoint(checkpoint_path, sig)
    results: dict[str, Optional[dict[str, Any]]] = dict(done)
    pending = [u for u in units if u.key not in done]

    print(f"\n{'=' * 56}")
    print("Processing summary")
    print(f"{'=' * 56}")
    print(f"Input rows (instances):  {len(rows)}")
    print(f"Total work units:        {len(units)}")
    print(f"Already done (skipped):  {len(units) - len(pending)}")
    print(f"To process now:          {len(pending)}")
    print(f"Model / deployment:      {az['deployment']}")
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
        try:
            await process(
                pending,
                results,
                client,
                az["deployment"],
                limiter,
                checkpoint_writer,
                proc["max_retries"],
                proc["retry_base_delay"],
            )
        finally:
            checkpoint_writer.close()
            await client.close()

    out_rows = assemble_rows(rows, results)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(files["output_dir"]) / f"{files['output_prefix']}_{timestamp}.csv"
    write_csv(out_rows, output_path)

    print(f"\nWrote {len(out_rows)} rows to: {output_path}")
    print(f"Checkpoint retained at: {checkpoint_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse bone marrow report sections.")
    default_config = str(Path(__file__).with_name("config.yaml"))
    parser.add_argument("--config", default=default_config, help="Path to config.yaml")
    parser.add_argument("--limit", type=int, default=None, help="Only process first N input rows (smoke test)")
    parser.add_argument("--fresh", action="store_true", help="Ignore/remove existing checkpoint")
    parser.add_argument("--concurrency", type=int, default=None, help="Override max concurrency")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    args = parser.parse_args()

    try:
        asyncio.run(async_main(args))
    except FileNotFoundError as exc:
        print(f"Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
