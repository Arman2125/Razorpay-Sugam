"""Regression coverage for the same timezone bug class fixed in
merchant_auth_service.py: ConversationState.expires_at is also
DateTime(timezone=True), so a row read back from Postgres has a
timezone-aware expires_at. get_active_state() compares it against "now" —
if "now" were naive (the old datetime.utcnow()), this raises
"TypeError: can't subtract offset-naive and offset-aware datetimes" for
every non-expired active state, i.e. the entire clarification/ambiguity
flow. Reproduced here with a mocked row, no live database needed."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import conversation_state_service


@pytest.mark.asyncio
async def test_active_unexpired_state_with_aware_expiry_is_returned_without_raising():
    session = MagicMock()
    state = MagicMock(expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    session.scalar = AsyncMock(return_value=state)

    result = await conversation_state_service.get_active_state(session, "+919876543210")  # must not raise

    assert result is state


@pytest.mark.asyncio
async def test_expired_state_with_aware_expiry_is_marked_expired_without_raising():
    session = MagicMock()
    state = MagicMock(expires_at=datetime.now(timezone.utc) - timedelta(minutes=5), status="active")
    session.scalar = AsyncMock(return_value=state)
    session.commit = AsyncMock()

    result = await conversation_state_service.get_active_state(session, "+919876543210")  # must not raise

    assert result is None
    assert state.status == "expired"
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_no_active_state_returns_none():
    session = MagicMock()
    session.scalar = AsyncMock(return_value=None)

    result = await conversation_state_service.get_active_state(session, "+919876543210")

    assert result is None
