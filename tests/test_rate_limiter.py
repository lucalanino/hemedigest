"""RateLimiter tests use a fake monotonic clock + a no-real-delay asyncio.sleep so that
sliding-window (60s) ceiling enforcement is exercised deterministically and fast.
"""

import asyncio

import pytest

from section_parser import parse_sections as ps


@pytest.fixture
def fake_clock(monkeypatch):
    state = {"now": 1000.0}
    real_sleep = asyncio.sleep  # captured before patching, to actually yield control

    def fake_monotonic():
        return state["now"]

    async def fake_sleep(seconds):
        state["now"] += seconds
        await real_sleep(0)

    monkeypatch.setattr(ps.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(ps.asyncio, "sleep", fake_sleep)
    return state


async def test_semaphore_limits_concurrency():
    limiter = ps.RateLimiter(max_concurrency=2, target_rpm=1000, target_tpm=1000000)
    await limiter.sem.acquire()
    await limiter.sem.acquire()
    assert limiter.sem.locked()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(limiter.sem.acquire(), timeout=0.05)
    limiter.sem.release()
    limiter.sem.release()


async def test_acquire_succeeds_immediately_within_limits(fake_clock):
    limiter = ps.RateLimiter(max_concurrency=10, target_rpm=100, target_tpm=100000)
    await limiter.acquire(500)
    assert limiter.blocked_events == 0
    assert limiter.blocked_seconds == 0.0
    assert limiter.rpm_blocks == 0
    assert limiter.tpm_blocks == 0


async def test_acquire_blocks_on_rpm_ceiling_until_window_slides(fake_clock):
    limiter = ps.RateLimiter(max_concurrency=100, target_rpm=2, target_tpm=1_000_000)
    await limiter.acquire(10)
    await limiter.acquire(10)  # fills the RPM ceiling, both at t=1000.0

    await limiter.acquire(10)  # must block until the 60s window purges the first two

    assert limiter.rpm_blocks >= 1
    assert limiter.tpm_blocks == 0
    assert limiter.blocked_events == 1
    assert limiter.blocked_seconds >= 60.0


async def test_acquire_blocks_on_tpm_ceiling_until_window_slides(fake_clock):
    limiter = ps.RateLimiter(max_concurrency=100, target_rpm=1_000_000, target_tpm=100)
    await limiter.acquire(80)  # tok_sum=80

    await limiter.acquire(80)  # 80+80 > 100 -> must block

    assert limiter.tpm_blocks >= 1
    assert limiter.rpm_blocks == 0
    assert limiter.blocked_events == 1
    assert limiter.blocked_seconds >= 60.0


async def test_acquire_clamps_oversized_request_to_target_tpm(fake_clock):
    limiter = ps.RateLimiter(max_concurrency=10, target_rpm=1000, target_tpm=100)
    await limiter.acquire(100_000)  # clamped to 100, fits an empty window, no blocking
    assert limiter.blocked_events == 0
    assert limiter.tpm_blocks == 0
