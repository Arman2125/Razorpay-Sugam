"""whatsapp_dedup_service.mark_seen relies on Postgres's
INSERT ... ON CONFLICT DO NOTHING rowcount semantics: rowcount == 1 means
this call's INSERT actually happened (first time this message_id has been
seen), rowcount == 0 means the row already existed (a duplicate webhook
delivery of the same message)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import whatsapp_dedup_service


@pytest.mark.asyncio
async def test_first_time_message_id_is_recorded_and_returns_true():
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=1))
    session.commit = AsyncMock()

    result = await whatsapp_dedup_service.mark_seen(session, "wamid.abc123")

    assert result is True
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_already_seen_message_id_conflicts_and_returns_false():
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=0))
    session.commit = AsyncMock()

    result = await whatsapp_dedup_service.mark_seen(session, "wamid.abc123")

    assert result is False
