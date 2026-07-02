"""Run-time logging, stats, and the end-of-run report for the section parser.

PHI discipline (non-negotiable): the input is bone-marrow pathology text with MRNs.
Nothing here ever logs report text, cell content, MRNs, or order identifiers -- only
content-hash work-unit keys, exception *type names*, and numeric metadata. The optional
``--log-file`` inherits the same rule: it holds error metadata, never source content.

Three pieces live here to keep ``parse_sections.py`` readable:

- ``TqdmLoggingHandler`` -- routes log records through ``tqdm.write`` so an active
  progress bar is never corrupted by an interleaved log line.
- ``setup_logging`` -- configures the ``section_parser`` logger (tqdm-safe console at
  the chosen level; optional append-mode file that always captures DEBUG).
- ``RunStats`` + ``format_run_report`` -- the counters accumulated during a run and the
  human-readable summary that tells the user which limit (semaphore / rate limiter /
  server 429s) is binding, so they can set the concurrency knobs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from tqdm import tqdm

LOGGER_NAME = "section_parser"

# Console default; file handler (if any) always captures DEBUG regardless.
_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


class TqdmLoggingHandler(logging.Handler):
    """Emit records via ``tqdm.write`` so the progress bar is not clobbered."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            tqdm.write(self.format(record))
        except Exception:  # noqa: BLE001 - never let logging crash the run
            self.handleError(record)


def setup_logging(level: str, log_file: str | None = None) -> logging.Logger:
    """Configure and return the ``section_parser`` logger.

    ``level`` gates the console (tqdm-safe) handler. When ``log_file`` is set an
    append-mode file handler is added that always records DEBUG, so a quiet console
    (WARNING) run still leaves a full metadata trail on disk. Idempotent: existing
    handlers are cleared so a re-run in the same process does not double-log.
    """
    logger = get_logger()
    logger.setLevel(logging.DEBUG)  # let each handler do its own filtering
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    console = TqdmLoggingHandler()
    console.setLevel(getattr(logging, level))
    console.setFormatter(fmt)
    logger.addHandler(console)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger


@dataclass
class RunStats:
    """Counters accumulated across the run.

    No lock: asyncio is single-threaded and every mutation happens between awaits
    (the in-flight gauge in particular is incremented and its peak captured with no
    await in between), so plain ``+= 1`` is atomic here.
    """

    parsed_ok: int = 0
    refusals: int = 0
    length_nulls: int = 0
    content_filter_nulls: int = 0
    exhausted_failures: int = 0
    retries: int = 0
    http_429: int = 0
    other_retryable: int = 0
    actual_tokens: int = 0
    calls: int = 0
    # In-flight gauge, measured at the call site (not the semaphore, whose occupancy
    # includes tasks parked in RateLimiter.acquire and so overstates in-flight work).
    inflight: int = 0
    peak_inflight: int = 0

    def note_inflight_start(self) -> None:
        self.inflight += 1
        if self.inflight > self.peak_inflight:
            self.peak_inflight = self.inflight

    def note_inflight_end(self) -> None:
        self.inflight -= 1


def _rate(count: float, elapsed: float) -> float:
    return (count / elapsed * 60.0) if elapsed > 0 else 0.0


def format_run_report(
    stats: RunStats,
    limiter,
    elapsed: float,
    max_concurrency: int,
    target_rpm: int,
    target_tpm: int,
    skipped_empty: int,
) -> str:
    """Build the end-of-run report: outcomes, throughput, and the binding constraint.

    ``limiter`` is the ``RateLimiter`` (read for its blocked_* / *_blocks counters).
    Printed rather than logged so it always shows regardless of console log level.
    """
    achieved_rpm = _rate(stats.calls, elapsed)
    achieved_tpm = _rate(stats.actual_tokens, elapsed)
    avg_tokens = (stats.actual_tokens / stats.calls) if stats.calls else 0

    lines: list[str] = []
    bar = "=" * 56
    lines.append(bar)
    lines.append("Run report")
    lines.append(bar)

    lines.append("Outcomes")
    lines.append(f"  Parsed OK:             {stats.parsed_ok}")
    lines.append(f"  Refusals:              {stats.refusals}")
    lines.append(f"  Length-truncated null: {stats.length_nulls}")
    lines.append(f"  Content-filtered null: {stats.content_filter_nulls}")
    lines.append(f"  Failed (retry rerun):  {stats.exhausted_failures}")
    lines.append(f"  Skipped empty/NA:      {skipped_empty}")

    lines.append("Retry / error events")
    lines.append(f"  Retries scheduled:     {stats.retries}")
    lines.append(f"  HTTP 429 responses:    {stats.http_429}")
    lines.append(f"  Other errors:          {stats.other_retryable}")

    lines.append("Throughput")
    lines.append(f"  Wall time:             {elapsed:.1f}s")
    lines.append(f"  Calls made:            {stats.calls}")
    lines.append(f"  Achieved RPM (avg):    {achieved_rpm:.0f} / {target_rpm} target")
    lines.append(f"  Achieved TPM (avg):    {achieved_tpm:.0f} / {target_tpm} target")
    lines.append(f"  Actual tokens total:   {stats.actual_tokens} (~{avg_tokens:.0f}/call)")

    lines.append("Limits")
    lines.append(f"  Peak in-flight:        {stats.peak_inflight} / {max_concurrency} max")
    lines.append(
        f"  Rate-limiter blocks:   {limiter.blocked_events} "
        f"(rpm {limiter.rpm_blocks}, tpm {limiter.tpm_blocks}), "
        f"{limiter.blocked_seconds:.1f}s waiting"
    )
    lines.append(f"  Server 429s:           {stats.http_429}")

    lines.append(f"Verdict: {_verdict(stats, limiter, max_concurrency)}")
    lines.append(bar)
    return "\n".join(lines)


def _verdict(stats: RunStats, limiter, max_concurrency: int) -> str:
    """One-line, actionable read on which limit is binding."""
    if stats.http_429 > 0:
        return (
            "server throttling (429s hit) -- lower target_rpm/target_tpm or "
            "--concurrency, or honor Retry-After."
        )
    if limiter.blocked_seconds >= 1.0:
        which = "RPM" if limiter.rpm_blocks >= limiter.tpm_blocks else "TPM"
        return (
            f"local rate limiter binding (mostly {which}) -- raise "
            f"target_{which.lower()} if your Azure quota allows."
        )
    if stats.peak_inflight >= max_concurrency:
        return (
            "semaphore binding (peak in-flight hit max) with rate-limit headroom -- "
            "raise --concurrency to go faster."
        )
    return "headroom on all limits -- not saturated; more input or concurrency would use it."
