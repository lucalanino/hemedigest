import logging
import os
from types import SimpleNamespace

from section_parser.runlog import (
    LOGGER_NAME,
    RunStats,
    _verdict,
    format_run_report,
    get_logger,
    setup_logging,
)


def make_limiter(blocked_seconds=0.0, blocked_events=0, rpm_blocks=0, tpm_blocks=0):
    return SimpleNamespace(
        blocked_seconds=blocked_seconds,
        blocked_events=blocked_events,
        rpm_blocks=rpm_blocks,
        tpm_blocks=tpm_blocks,
    )


def test_note_inflight_tracks_current_and_peak():
    stats = RunStats()
    stats.note_inflight_start()
    stats.note_inflight_start()
    assert stats.inflight == 2
    assert stats.peak_inflight == 2
    stats.note_inflight_end()
    assert stats.inflight == 1
    assert stats.peak_inflight == 2  # peak retained after a decrement
    stats.note_inflight_start()
    assert stats.inflight == 2
    assert stats.peak_inflight == 2  # back to the prior peak, not exceeded


def test_verdict_server_429s_takes_priority():
    stats = RunStats(http_429=3)
    limiter = make_limiter(blocked_seconds=5.0, rpm_blocks=1)
    assert "server throttling" in _verdict(stats, limiter, max_concurrency=20)


def test_verdict_rpm_dominant_local_limiter():
    stats = RunStats()
    limiter = make_limiter(blocked_seconds=2.0, rpm_blocks=5, tpm_blocks=1)
    verdict = _verdict(stats, limiter, max_concurrency=20)
    assert "local rate limiter binding" in verdict
    assert "RPM" in verdict


def test_verdict_tpm_dominant_local_limiter():
    stats = RunStats()
    limiter = make_limiter(blocked_seconds=2.0, rpm_blocks=1, tpm_blocks=5)
    verdict = _verdict(stats, limiter, max_concurrency=20)
    assert "local rate limiter binding" in verdict
    assert "TPM" in verdict


def test_verdict_semaphore_binding():
    stats = RunStats(peak_inflight=20)
    limiter = make_limiter()
    verdict = _verdict(stats, limiter, max_concurrency=20)
    assert "semaphore binding" in verdict


def test_verdict_headroom():
    stats = RunStats(peak_inflight=5)
    limiter = make_limiter()
    verdict = _verdict(stats, limiter, max_concurrency=20)
    assert "headroom" in verdict


def test_format_run_report_includes_key_figures():
    stats = RunStats(parsed_ok=7, calls=8, actual_tokens=1000)
    limiter = make_limiter()
    report = format_run_report(
        stats,
        limiter,
        elapsed=10.0,
        max_concurrency=20,
        target_rpm=2000,
        target_tpm=200000,
        skipped_empty=2,
    )
    assert "Parsed OK:             7" in report
    assert "Calls made:            8" in report
    assert "Skipped empty/NA:      2" in report
    assert "Verdict:" in report


def test_setup_logging_returns_named_logger():
    logger = setup_logging("WARNING")
    assert logger is get_logger()
    assert logger.name == LOGGER_NAME


def test_setup_logging_is_idempotent_across_calls():
    setup_logging("WARNING")
    logger = setup_logging("DEBUG")
    console_handlers = [h for h in logger.handlers if not isinstance(h, logging.FileHandler)]
    assert len(console_handlers) == 1
    assert console_handlers[0].level == logging.DEBUG


def test_setup_logging_adds_file_handler(tmp_path):
    log_file = tmp_path / "run.log"
    logger = setup_logging("WARNING", str(log_file))
    file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) == 1
    assert file_handlers[0].baseFilename == os.path.abspath(str(log_file))
    # cleanup so later tests re-adding handlers don't leak this file handle
    setup_logging("WARNING")
