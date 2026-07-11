"""run_unit/process retry-and-backoff state machine, with client.chat.completions.parse
mocked out -- no real network/API call is ever made.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import ContentFilterFinishReasonError, LengthFinishReasonError, RateLimitError

from section_parser.parse_sections import RateLimiter, WorkUnit, process, run_unit
from section_parser.runlog import RunStats
from section_parser.schemas.biopsy import SCHEMA as BIOPSY_SCHEMA


def make_completion(parsed=None, refusal=None, tokens=100):
    message = SimpleNamespace(refusal=refusal, parsed=parsed)
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(total_tokens=tokens)
    return SimpleNamespace(choices=[choice], usage=usage)


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
def fake_client():
    client = SimpleNamespace()
    client.chat = SimpleNamespace()
    client.chat.completions = SimpleNamespace()
    client.chat.completions.parse = AsyncMock()
    return client


@pytest.fixture
def unit():
    return WorkUnit("unit-key-1", BIOPSY_SCHEMA, "system prompt", "report text")


@pytest.fixture
def generous_limiter():
    return RateLimiter(max_concurrency=10, target_rpm=10_000, target_tpm=10_000_000)


async def run_it(unit, fake_client, limiter, max_retries=3, retry_base_delay=0.0):
    checkpoint = FakeCheckpoint()
    results: dict = {}
    stats = RunStats()
    pbar = FakePbar()
    await run_unit(
        unit, fake_client, "gpt-5.4", limiter, checkpoint, results, stats,
        max_retries, retry_base_delay, "low", pbar,
    )
    return checkpoint, results, stats, pbar


async def test_success_on_first_attempt(fake_client, unit, generous_limiter):
    fake_client.chat.completions.parse.return_value = make_completion(
        parsed=BIOPSY_SCHEMA(cellularity_pct=60), tokens=123
    )
    checkpoint, results, stats, pbar = await run_it(unit, fake_client, generous_limiter)

    assert stats.parsed_ok == 1
    assert stats.calls == 1
    assert stats.actual_tokens == 123
    assert results[unit.key]["cellularity_pct"] == 60
    assert checkpoint.writes == [(unit.key, results[unit.key])]
    assert pbar.updates == 1


async def test_refusal_records_null_but_still_checkpoints(fake_client, unit, generous_limiter):
    fake_client.chat.completions.parse.return_value = make_completion(refusal="cannot help")
    checkpoint, results, stats, pbar = await run_it(unit, fake_client, generous_limiter)

    assert stats.refusals == 1
    assert stats.parsed_ok == 0
    assert results[unit.key] is None
    assert checkpoint.writes == [(unit.key, None)]


async def test_rate_limit_then_success(fake_client, unit, generous_limiter):
    fake_client.chat.completions.parse.side_effect = [
        make_rate_limit_error(),
        make_completion(parsed=BIOPSY_SCHEMA(cellularity_pct=50)),
    ]
    checkpoint, results, stats, pbar = await run_it(unit, fake_client, generous_limiter)

    assert stats.http_429 == 1
    assert stats.retries == 1
    assert stats.parsed_ok == 1
    assert results[unit.key]["cellularity_pct"] == 50
    # only one checkpoint write -- on the eventual success, not the failed attempt
    assert len(checkpoint.writes) == 1


async def test_rate_limit_exhausts_retries(fake_client, unit, generous_limiter):
    fake_client.chat.completions.parse.side_effect = [make_rate_limit_error() for _ in range(3)]
    checkpoint, results, stats, pbar = await run_it(
        unit, fake_client, generous_limiter, max_retries=2
    )

    assert stats.http_429 == 3
    assert stats.retries == 2
    assert stats.exhausted_failures == 1
    assert stats.parsed_ok == 0
    # in-memory results still gets an entry (used for this run's CSV) but nothing
    # is checkpointed, so a re-run without --fresh will retry this unit
    assert unit.key in results
    assert results[unit.key] is None
    assert checkpoint.writes == []


async def test_length_finish_reason_error_is_non_retryable(fake_client, unit, generous_limiter):
    fake_client.chat.completions.parse.side_effect = LengthFinishReasonError(
        completion=SimpleNamespace(usage=None)
    )
    checkpoint, results, stats, pbar = await run_it(unit, fake_client, generous_limiter)

    assert stats.length_nulls == 1
    assert stats.calls == 1  # no retry attempted
    assert results[unit.key] is None
    assert checkpoint.writes == [(unit.key, None)]


async def test_content_filter_error_is_non_retryable(fake_client, unit, generous_limiter):
    fake_client.chat.completions.parse.side_effect = ContentFilterFinishReasonError()
    checkpoint, results, stats, pbar = await run_it(unit, fake_client, generous_limiter)

    assert stats.content_filter_nulls == 1
    assert stats.calls == 1
    assert results[unit.key] is None
    assert checkpoint.writes == [(unit.key, None)]


async def test_process_runs_multiple_units_and_populates_results(fake_client, generous_limiter):
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
        units, results, stats, fake_client, "gpt-5.4", generous_limiter, checkpoint,
        max_retries=3, retry_base_delay=0.0, reasoning_effort="low",
    )

    assert results["k1"]["cellularity_pct"] == 10
    assert results["k2"]["cellularity_pct"] == 20
    assert results["k3"] is None
    assert stats.parsed_ok == 2
    assert stats.refusals == 1
    assert len(checkpoint.writes) == 3
