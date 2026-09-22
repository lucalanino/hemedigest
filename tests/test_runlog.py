import logging
import os

from section_parser.runlog import (
    LOGGER_NAME,
    RunStats,
    _verdict,
    format_run_report,
    get_logger,
    setup_logging,
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


def test_verdict_fatal_4xx_takes_priority():
    stats = RunStats(fatal_api_errors=3, http_429=1, peak_inflight=20)
    assert "non-retryable 4xx" in _verdict(stats, max_concurrency=20)


def test_verdict_server_429s():
    stats = RunStats(http_429=3)
    verdict = _verdict(stats, max_concurrency=20)
    assert "server throttling" in verdict
    assert "3 429s" in verdict


def test_verdict_concurrency_binding():
    stats = RunStats(peak_inflight=20)
    assert "concurrency binding" in _verdict(stats, max_concurrency=20)


def test_verdict_not_saturated():
    stats = RunStats(peak_inflight=5)
    assert "not saturated" in _verdict(stats, max_concurrency=20)


def test_format_run_report_includes_key_figures():
    stats = RunStats(parsed_ok=7, calls=8, actual_tokens=1000, peak_inflight=4)
    report = format_run_report(stats, elapsed=10.0, max_concurrency=20, skipped_empty=2)
    assert "Parsed OK:             7" in report
    assert "Calls made:            8" in report
    assert "Skipped empty/NA:      2" in report
    assert "Peak in-flight:        4 / 20 max" in report
    assert "Verdict:" in report


def test_format_run_report_has_no_rate_limiter_section():
    report = format_run_report(RunStats(), elapsed=1.0, max_concurrency=20, skipped_empty=0)
    for gone in ("Rate-limiter", "target", "RPM (avg):    0 /"):
        assert gone not in report


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
    try:
        file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 1
        assert file_handlers[0].baseFilename == os.path.abspath(str(log_file))
    finally:
        # always close the FileHandler, even on assertion failure, or tmp_path
        # teardown can hit a PermissionError on Windows that masks the real failure
        setup_logging("WARNING")
