"""run_unit/process retry-and-backoff state machine, with client.chat.completions.parse
mocked out -- no real network/API call is ever made.
"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from openai import (
    AuthenticationError,
    BadRequestError,
    ContentFilterFinishReasonError,
    LengthFinishReasonError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

from section_parser import parse_sections as ps
from section_parser.parse_sections import WorkUnit, process, run_unit
from section_parser.runlog import RunStats
from section_parser.schemas.biopsy import SCHEMA as BIOPSY_SCHEMA
from tests.conftest import make_completion


def make_rate_limit_error():
    response = httpx.Response(status_code=429, request=httpx.Request("POST", "https://example.com"))
    return RateLimitError("rate limited", response=response, body=None)


class FakeCheckpoint:
    def __init__(self):
        self.writes: list[tuple[str, object]] = []

    async def write(self, key, result):
        self.writes.append((key, result))


class FakePbar:
    def __init__(self):
        self.updates = 0

    def update(self, n=1):
        self.updates += n


@pytest.fixture
def unit():
    return WorkUnit("unit-key-1", BIOPSY_SCHEMA, "system prompt", "report text")


@pytest.fixture
def sem():
    return asyncio.Semaphore(10)


async def run_it(unit, fake_client, sem, max_retries=3, retry_base_delay=0.0):
    checkpoint = FakeCheckpoint()
    results: dict = {}
    stats = RunStats()
    pbar = FakePbar()
    await run_unit(
        unit,
        fake_client,
        "gpt-5.4",
        sem,
        checkpoint,
        results,
        stats,
        max_retries,
        retry_base_delay,
        "low",
        pbar,
    )
    return checkpoint, results, stats, pbar


async def test_success_on_first_attempt(fake_client, unit, sem):
    fake_client.chat.completions.parse.return_value = make_completion(
        parsed=BIOPSY_SCHEMA(cellularity_pct=60), tokens=123
    )
    checkpoint, results, stats, pbar = await run_it(unit, fake_client, sem)

    assert stats.parsed_ok == 1
    assert stats.calls == 1
    assert stats.actual_tokens == 123
    assert results[unit.key]["cellularity_pct"] == 60
    assert checkpoint.writes == [(unit.key, results[unit.key])]
    assert pbar.updates == 1
    assert stats.inflight == 0
    assert stats.peak_inflight == 1


async def test_refusal_records_null_but_still_checkpoints(fake_client, unit, sem):
    fake_client.chat.completions.parse.return_value = make_completion(refusal="cannot help")
    checkpoint, results, stats, pbar = await run_it(unit, fake_client, sem)

    assert stats.refusals == 1
    assert stats.parsed_ok == 0
    assert results[unit.key] is None
    assert checkpoint.writes == [(unit.key, None)]


async def test_rate_limit_then_success(fake_client, unit, sem):
    fake_client.chat.completions.parse.side_effect = [
        make_rate_limit_error(),
        make_completion(parsed=BIOPSY_SCHEMA(cellularity_pct=50)),
    ]
    checkpoint, results, stats, pbar = await run_it(unit, fake_client, sem)

    assert stats.http_429 == 1
    assert stats.retries == 1
    assert stats.parsed_ok == 1
    assert results[unit.key]["cellularity_pct"] == 50
    # only one checkpoint write -- on the eventual success, not the failed attempt
    assert len(checkpoint.writes) == 1


async def test_rate_limit_exhausts_retries(fake_client, unit, sem):
    fake_client.chat.completions.parse.side_effect = [make_rate_limit_error() for _ in range(3)]
    checkpoint, results, stats, pbar = await run_it(unit, fake_client, sem, max_retries=2)

    assert stats.http_429 == 3
    assert stats.retries == 2
    assert stats.exhausted_failures == 1
    assert stats.parsed_ok == 0
    # in-memory results still gets an entry (used for this run's CSV) but nothing
    # is checkpointed, so a re-run without --fresh will retry this unit
    assert unit.key in results
    assert results[unit.key] is None
    assert checkpoint.writes == []
    # note_inflight_end() must run in a `finally`, even on the exhausted/break path,
    # or a regression there would leak the inflight counter across retries
    assert stats.inflight == 0
    assert stats.peak_inflight == 1


async def test_length_finish_reason_error_is_non_retryable(fake_client, unit, sem):
    fake_client.chat.completions.parse.side_effect = LengthFinishReasonError(completion=SimpleNamespace(usage=None))
    checkpoint, results, stats, pbar = await run_it(unit, fake_client, sem)

    assert stats.length_nulls == 1
    assert stats.calls == 1  # no retry attempted
    assert results[unit.key] is None
    assert checkpoint.writes == [(unit.key, None)]


async def test_content_filter_error_is_non_retryable(fake_client, unit, sem):
    fake_client.chat.completions.parse.side_effect = ContentFilterFinishReasonError()
    checkpoint, results, stats, pbar = await run_it(unit, fake_client, sem)

    assert stats.content_filter_nulls == 1
    assert stats.calls == 1
    assert results[unit.key] is None
    assert checkpoint.writes == [(unit.key, None)]


async def test_process_runs_multiple_units_and_populates_results(fake_client, sem):
    units = [
        WorkUnit("k1", BIOPSY_SCHEMA, "prompt", "text-1"),
        WorkUnit("k2", BIOPSY_SCHEMA, "prompt", "text-2"),
        WorkUnit("k3", BIOPSY_SCHEMA, "prompt", "text-3"),
    ]
    outcomes = {
        "text-1": make_completion(parsed=BIOPSY_SCHEMA(cellularity_pct=10)),
        "text-2": make_completion(parsed=BIOPSY_SCHEMA(cellularity_pct=20)),
        "text-3": make_completion(refusal="no"),
    }

    async def fake_parse(*, model, messages, response_format, reasoning_effort):
        text = messages[1]["content"]
        return outcomes[text]

    fake_client.chat.completions.parse.side_effect = fake_parse

    checkpoint = FakeCheckpoint()
    results: dict = {}
    stats = RunStats()

    await process(
        units,
        results,
        stats,
        fake_client,
        "gpt-5.4",
        sem,
        checkpoint,
        max_retries=3,
        retry_base_delay=0.0,
        reasoning_effort="low",
    )

    assert results["k1"]["cellularity_pct"] == 10
    assert results["k2"]["cellularity_pct"] == 20
    assert results["k3"] is None
    assert stats.parsed_ok == 2
    assert stats.refusals == 1
    assert len(checkpoint.writes) == 3


async def test_semaphore_slot_is_released_while_backing_off(fake_client, unit, sem, monkeypatch):
    """The backoff sleep runs outside the slot, so a retrying unit doesn't block others."""
    free_during_backoff: list[int] = []

    async def fake_sleep(seconds):
        free_during_backoff.append(sem._value)

    monkeypatch.setattr(ps.asyncio, "sleep", fake_sleep)

    fake_client.chat.completions.parse.side_effect = [
        make_rate_limit_error(),
        make_completion(parsed=BIOPSY_SCHEMA(cellularity_pct=50)),
    ]
    await run_it(unit, fake_client, sem)

    assert free_during_backoff == [10], "all ten slots should be free while waiting to retry"


async def test_retry_after_header_overrides_the_backoff_curve(fake_client, unit, sem, monkeypatch):
    """A 429 carrying Retry-After waits the server's figure when it is the longer one."""
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(ps.asyncio, "sleep", fake_sleep)

    response = httpx.Response(
        status_code=429,
        headers={"retry-after": "30"},
        request=httpx.Request("POST", "https://example.com"),
    )
    fake_client.chat.completions.parse.side_effect = [
        RateLimitError("slow down", response=response, body=None),
        make_completion(parsed=BIOPSY_SCHEMA(cellularity_pct=50)),
    ]
    await run_it(unit, fake_client, sem, retry_base_delay=1.0)

    assert slept == [30.0]  # not the 1.0 the curve would have chosen


async def test_backoff_curve_wins_when_retry_after_is_shorter(fake_client, unit, sem, monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(ps.asyncio, "sleep", fake_sleep)

    response = httpx.Response(
        status_code=429,
        headers={"retry-after-ms": "250"},
        request=httpx.Request("POST", "https://example.com"),
    )
    fake_client.chat.completions.parse.side_effect = [
        RateLimitError("slow down", response=response, body=None),
        make_completion(parsed=BIOPSY_SCHEMA(cellularity_pct=50)),
    ]
    await run_it(unit, fake_client, sem, retry_base_delay=4.0)

    assert slept == [4.0]  # 0.25s from the header is shorter, so the curve holds


async def test_retry_backoff_follows_exponential_formula(fake_client, unit, sem, monkeypatch):
    real_sleep = asyncio.sleep
    sleep_calls: list[float] = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(ps.asyncio, "sleep", fake_sleep)

    fake_client.chat.completions.parse.side_effect = [make_rate_limit_error() for _ in range(3)]
    await run_it(unit, fake_client, sem, max_retries=2, retry_base_delay=1.0)

    # retry_base_delay * 2**attempt for each retryable, non-final attempt (0, 1);
    # the final, exhausted attempt breaks out without sleeping again
    assert sleep_calls == [1.0, 2.0]


def make_bad_request_error(code=None, message="Unrecognized request argument supplied: reasoning_effort"):
    response = httpx.Response(status_code=400, request=httpx.Request("POST", "https://example.com"))
    body = {"message": message, "code": code, "param": "prompt", "type": None} if code else None
    return BadRequestError(message, response=response, body=body)


async def test_bad_request_fails_fast_without_retrying(fake_client, unit, sem):
    """A deterministic 400 must not burn the retry budget."""
    fake_client.chat.completions.parse.side_effect = [make_bad_request_error() for _ in range(5)]
    checkpoint, results, stats, pbar = await run_it(unit, fake_client, sem, max_retries=4)

    assert stats.calls == 1  # one attempt, not max_retries + 1
    assert stats.fatal_api_errors == 1
    assert stats.retries == 0
    assert stats.other_retryable == 0
    assert checkpoint.writes == []  # re-queued for the next run once the config is fixed
    assert pbar.updates == 1


@pytest.mark.parametrize(
    "exc_factory",
    [
        lambda: AuthenticationError(
            "token rejected",
            response=httpx.Response(401, request=httpx.Request("POST", "https://example.com")),
            body=None,
        ),
        lambda: PermissionDeniedError(
            "missing role",
            response=httpx.Response(403, request=httpx.Request("POST", "https://example.com")),
            body=None,
        ),
        lambda: NotFoundError(
            "deployment not found",
            response=httpx.Response(404, request=httpx.Request("POST", "https://example.com")),
            body=None,
        ),
    ],
    ids=["401", "403", "404"],
)
async def test_auth_and_deployment_errors_fail_fast(fake_client, unit, sem, exc_factory):
    fake_client.chat.completions.parse.side_effect = [exc_factory()]
    checkpoint, results, stats, pbar = await run_it(unit, fake_client, sem, max_retries=4)

    assert stats.calls == 1
    assert stats.fatal_api_errors == 1
    assert checkpoint.writes == []


async def test_prompt_content_filter_400_records_null_and_checkpoints(fake_client, unit, sem):
    """A 400 carrying code=content_filter is a permanent null, not a config error."""
    fake_client.chat.completions.parse.side_effect = [make_bad_request_error(code="content_filter")]
    checkpoint, results, stats, pbar = await run_it(unit, fake_client, sem)

    assert stats.content_filter_nulls == 1
    assert stats.fatal_api_errors == 0
    assert results[unit.key] is None
    assert checkpoint.writes == [(unit.key, None)]


async def test_first_seen_reports_each_error_kind_once():
    stats = RunStats()
    assert stats.first_seen("fatal:400") is True
    assert stats.first_seen("fatal:400") is False
    assert stats.first_seen("fatal:401") is True
