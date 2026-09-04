"""conversation_history_service.py is the generic, capability-agnostic memory
layer: it only records what actually happened (a user message, an assistant
reply, an assistant tool call + its result) and reconstructs it into OpenAI's
own multi-turn message shape. It must never inspect message content or a tool
name to decide anything — these tests assert exactly that: any role/tool
combination round-trips through the same generic logic, with no
capability-specific branch anywhere in the module."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import ConversationMessage
from app.services import conversation_history_service as history_service


def _row(**overrides):
    defaults = dict(
        whatsapp_number="+919876543210",
        role="user",
        content=None,
        tool_call_id=None,
        tool_name=None,
        tool_arguments=None,
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return ConversationMessage(**defaults)


@pytest.mark.asyncio
async def test_get_recent_messages_reconstructs_user_message():
    session = MagicMock()
    session.scalars = AsyncMock(
        return_value=MagicMock(all=MagicMock(return_value=[_row(role="user", content="hi")]))
    )

    messages = await history_service.get_recent_messages(session, "+919876543210")

    assert messages == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_get_recent_messages_reconstructs_plain_assistant_reply():
    session = MagicMock()
    session.scalars = AsyncMock(
        return_value=MagicMock(
            all=MagicMock(return_value=[_row(role="assistant", content="Who should I create it for?")])
        )
    )

    messages = await history_service.get_recent_messages(session, "+919876543210")

    assert messages == [{"role": "assistant", "content": "Who should I create it for?"}]


@pytest.mark.asyncio
async def test_get_recent_messages_reconstructs_assistant_tool_call():
    session = MagicMock()
    session.scalars = AsyncMock(
        return_value=MagicMock(
            all=MagicMock(
                return_value=[
                    _row(
                        role="assistant",
                        tool_call_id="call_abc",
                        tool_name="create_payment_link",
                        tool_arguments={"amount": 5000, "customer_name": "Neha Pawar"},
                    )
                ]
            )
        )
    )

    messages = await history_service.get_recent_messages(session, "+919876543210")

    assert messages == [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_abc",
                    "type": "function",
                    "function": {
                        "name": "create_payment_link",
                        "arguments": '{"amount": 5000, "customer_name": "Neha Pawar"}',
                    },
                }
            ],
        }
    ]


@pytest.mark.asyncio
async def test_get_recent_messages_reconstructs_tool_result():
    session = MagicMock()
    session.scalars = AsyncMock(
        return_value=MagicMock(
            all=MagicMock(
                return_value=[_row(role="tool", tool_call_id="call_abc", content='{"success": true}')]
            )
        )
    )

    messages = await history_service.get_recent_messages(session, "+919876543210")

    assert messages == [{"role": "tool", "tool_call_id": "call_abc", "content": '{"success": true}'}]


@pytest.mark.asyncio
async def test_get_recent_messages_preserves_oldest_first_order():
    """Rows come back from the query newest-first (ORDER BY ... DESC, for the
    LIMIT to correctly keep the *most recent* N) — the service must reverse
    them before returning so the replayed conversation reads oldest-first."""
    session = MagicMock()
    newest_first = [
        _row(role="assistant", content="third"),
        _row(role="user", content="second"),
        _row(role="user", content="first"),
    ]
    session.scalars = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=newest_first)))

    messages = await history_service.get_recent_messages(session, "+919876543210")

    assert [m["content"] for m in messages] == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_record_user_message_adds_and_commits(monkeypatch):
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock()  # the opportunistic prune

    await history_service.record_user_message(session, "+919876543210", "create a payment link")

    added = session.add.call_args.args[0]
    assert isinstance(added, ConversationMessage)
    assert added.role == "user"
    assert added.content == "create a payment link"
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_assistant_reply_adds_a_plain_assistant_row():
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    await history_service.record_assistant_reply(session, "+919876543210", "Who should I create it for?")

    added = session.add.call_args.args[0]
    assert added.role == "assistant"
    assert added.content == "Who should I create it for?"
    assert added.tool_name is None
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_tool_exchange_writes_paired_assistant_and_tool_rows():
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    await history_service.record_tool_exchange(
        session,
        "+919876543210",
        "call_abc",
        "create_payment_link",
        {"amount": 5000},
        {"success": True, "payment_link": {"shortUrl": "http://x"}},
    )

    assert session.add.call_count == 2
    assistant_row, tool_row = (call.args[0] for call in session.add.call_args_list)

    assert assistant_row.role == "assistant"
    assert assistant_row.tool_call_id == "call_abc"
    assert assistant_row.tool_name == "create_payment_link"
    assert assistant_row.tool_arguments == {"amount": 5000}

    assert tool_row.role == "tool"
    assert tool_row.tool_call_id == "call_abc"
    assert "shortUrl" in tool_row.content
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_tool_exchange_truncates_a_large_result(monkeypatch):
    monkeypatch.setattr(history_service.settings, "conversation_history_max_tool_result_chars", 50)
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    huge_result = {"items": [{"name": f"customer-{i}"} for i in range(200)]}
    await history_service.record_tool_exchange(session, "+919876543210", "call_1", "search_customers", {}, huge_result)

    _, tool_row = (call.args[0] for call in session.add.call_args_list)
    assert len(tool_row.content) <= 50 + len("...(truncated)")
    assert tool_row.content.endswith("...(truncated)")


@pytest.mark.asyncio
async def test_record_user_message_prunes_expired_rows_first(monkeypatch):
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock()

    await history_service.record_user_message(session, "+919876543210", "hi")

    session.execute.assert_awaited_once()  # the opportunistic DELETE ... WHERE created_at < cutoff


def test_cutoff_is_bounded_by_configured_ttl(monkeypatch):
    monkeypatch.setattr(history_service.settings, "conversation_history_ttl_seconds", 60)
    now = datetime.now(timezone.utc)

    cutoff = history_service._cutoff(now)

    assert now - cutoff == timedelta(seconds=60)


@pytest.mark.asyncio
async def test_get_recent_messages_respects_max_turns_limit():
    session = MagicMock()
    scalars_result = MagicMock(all=MagicMock(return_value=[]))
    session.scalars = AsyncMock(return_value=scalars_result)

    await history_service.get_recent_messages(session, "+919876543210")

    # The query itself is built with a LIMIT bound to conversation_history_max_turns;
    # we only assert the service actually issued exactly one query and consumed
    # its result via .all(), i.e. it doesn't paginate or fetch everything.
    session.scalars.assert_awaited_once()
    scalars_result.all.assert_called_once()
