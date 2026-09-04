"""run_periodic() must sleep for the configured interval BEFORE its first
sync — main.py's lifespan already performs one synchronous sync on startup,
so an immediate first sync here would double every startup call (this is
exactly what produced the two back-to-back requests that tripped
Mini-Razorpay's 429). A failed sync must also never stop the loop."""

from unittest.mock import AsyncMock

import pytest

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
