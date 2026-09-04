"""run_periodic() must sleep for the configured interval BEFORE its first
sync — main.py's lifespan already performs one synchronous sync on startup,
so an immediate first sync here would double every startup call (this is
exactly what produced the two back-to-back requests that tripped
Mini-Razorpay's 429). A failed sync must also never stop the loop.

_fetch_demo_merchants() is covered separately below: Mini-Razorpay's
demo-merchants endpoint has been observed returning a transient 429 that a
moments-later retry clears, so a small bounded retry (Retry-After if given,
else bounded backoff) is expected to absorb that without ever hammering the
endpoint or blocking startup for long."""

import httpx
import pytest

from unittest.mock import AsyncMock

from app.services import directory_sync_service as sync_service


class _StopLoop(Exception):
    """Raised from the mocked sleep to end run_periodic's `while True` after
    the iterations under test, since it never returns on its own."""


@pytest.mark.asyncio
async def test_first_sync_waits_for_the_interval_not_immediate(monkeypatch):
    calls = []

    async def fake_sleep(seconds):
        calls.append(("sleep", seconds))
        if len([c for c in calls if c[0] == "sleep"]) >= 2:
            raise _StopLoop

    mock_sync_once = AsyncMock(side_effect=lambda session: calls.append(("sync", None)))
    monkeypatch.setattr(sync_service.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(sync_service, "sync_once", mock_sync_once)

    session_factory = lambda: _FakeSessionCtx()

    with pytest.raises(_StopLoop):
        await sync_service.run_periodic(session_factory, 300)

    # sleep must happen before the first sync — no immediate sync on entry.
    assert calls[0] == ("sleep", 300)
    assert calls[1][0] == "sync"
    assert calls[2] == ("sleep", 300)
    assert mock_sync_once.await_count == 1


@pytest.mark.asyncio
async def test_failed_sync_does_not_stop_the_periodic_loop(monkeypatch):
    calls = []

    async def fake_sleep(seconds):
        calls.append("sleep")
        if calls.count("sleep") >= 3:
            raise _StopLoop

    mock_sync_once = AsyncMock(side_effect=RuntimeError("Mini-Razorpay returned 429"))
    monkeypatch.setattr(sync_service.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(sync_service, "sync_once", mock_sync_once)

    session_factory = lambda: _FakeSessionCtx()

    with pytest.raises(_StopLoop):
        await sync_service.run_periodic(session_factory, 300)

    # Two failed sync attempts, both swallowed — the loop kept going.
    assert mock_sync_once.await_count == 2


class _FakeSessionCtx:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc_info):
        return False


def _response(status_code: int, *, json_body: dict | None = None, retry_after: str | None = None) -> httpx.Response:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    request = httpx.Request("GET", sync_service.DEMO_MERCHANTS_URL)
    return httpx.Response(status_code, headers=headers, json=json_body, request=request)


def _patch_client_with_responses(monkeypatch, responses: list[httpx.Response]):
    """Replaces httpx.AsyncClient with a fake that hands back one queued
    response per GET, in order — regardless of how many separate
    `async with httpx.AsyncClient(...) as client:` blocks _fetch_demo_merchants
    opens across retries, since it opens a fresh one per attempt."""

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def get(self, url):
            return responses.pop(0)

    monkeypatch.setattr(sync_service.httpx, "AsyncClient", _FakeAsyncClient)


def _patch_no_op_sleep(monkeypatch) -> list[float]:
    """Records requested retry delays without actually waiting, so retry
    tests run instantly regardless of backoff/Retry-After values."""
    delays: list[float] = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(sync_service.asyncio, "sleep", fake_sleep)
    return delays


@pytest.mark.asyncio
async def test_fetch_succeeds_on_first_attempt_without_sleeping(monkeypatch):
    delays = _patch_no_op_sleep(monkeypatch)
    _patch_client_with_responses(monkeypatch, [_response(200, json_body={"data": [{"merchantId": "mer_1"}]})])

    body = await sync_service._fetch_demo_merchants()

    assert body == {"data": [{"merchantId": "mer_1"}]}
    assert delays == []  # no retry needed, no artificial delay incurred


@pytest.mark.asyncio
async def test_429_then_success_retries_and_returns_the_successful_body(monkeypatch):
    delays = _patch_no_op_sleep(monkeypatch)
    _patch_client_with_responses(
        monkeypatch,
        [
            _response(429),
            _response(200, json_body={"data": [{"merchantId": "mer_2"}]}),
        ],
    )

    body = await sync_service._fetch_demo_merchants()

    assert body == {"data": [{"merchantId": "mer_2"}]}
    assert len(delays) == 1  # exactly one backoff wait between the two attempts


@pytest.mark.asyncio
async def test_429_with_retry_after_header_honors_it_instead_of_backoff(monkeypatch):
    delays = _patch_no_op_sleep(monkeypatch)
    _patch_client_with_responses(
        monkeypatch,
        [
            _response(429, retry_after="7"),
            _response(200, json_body={"data": []}),
        ],
    )

    body = await sync_service._fetch_demo_merchants()

    assert body == {"data": []}
    assert delays == [7.0]  # the server's own Retry-After value, not the backoff schedule


@pytest.mark.asyncio
async def test_retry_after_above_the_cap_is_bounded(monkeypatch):
    delays = _patch_no_op_sleep(monkeypatch)
    _patch_client_with_responses(
        monkeypatch,
        [
            _response(429, retry_after="3600"),
            _response(200, json_body={"data": []}),
        ],
    )

    await sync_service._fetch_demo_merchants()

    assert delays == [sync_service._MAX_RETRY_AFTER_SECONDS]  # never honors an unreasonably large value verbatim


@pytest.mark.asyncio
async def test_repeated_429_exhausts_retries_and_raises_without_hanging(monkeypatch):
    delays = _patch_no_op_sleep(monkeypatch)
    responses = [_response(429) for _ in range(sync_service._MAX_SYNC_ATTEMPTS)]
    _patch_client_with_responses(monkeypatch, responses)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await sync_service._fetch_demo_merchants()

    assert exc_info.value.response.status_code == 429
    # Retried between attempts but never after the final one — bounded, not endless.
    assert len(delays) == sync_service._MAX_SYNC_ATTEMPTS - 1


@pytest.mark.asyncio
async def test_non_429_error_status_is_never_retried(monkeypatch):
    delays = _patch_no_op_sleep(monkeypatch)
    _patch_client_with_responses(monkeypatch, [_response(500)])

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await sync_service._fetch_demo_merchants()

    assert exc_info.value.response.status_code == 500
    assert delays == []  # a single attempt, no retry loop entered for a non-429 failure


@pytest.mark.asyncio
async def test_sync_once_raising_after_exhausted_retries_does_not_crash_run_periodic(monkeypatch):
    """End-to-end: even the new retry-exhausted failure mode is still just
    an exception sync_once raises — run_periodic's existing non-fatal
    handling (already covered above for a plain RuntimeError) swallows it
    identically, so the app never crashes over this."""
    calls = []

    async def fake_sleep(seconds):
        calls.append("sleep")
        if calls.count("sleep") >= 2:
            raise _StopLoop

    mock_sync_once = AsyncMock(side_effect=httpx.HTTPStatusError("429", request=None, response=None))
    monkeypatch.setattr(sync_service.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(sync_service, "sync_once", mock_sync_once)

    session_factory = lambda: _FakeSessionCtx()

    with pytest.raises(_StopLoop):
        await sync_service.run_periodic(session_factory, 300)

    assert mock_sync_once.await_count == 1
