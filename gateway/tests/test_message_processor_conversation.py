"""Proves the generalized multi-turn conversation architecture at the
orchestrator level: _process() must (a) fetch the real recent transcript and
hand it to intent_service.select_tool() ahead of the new message, and (b)
record every branch's outcome (a plain reply, or a tool call + its result)
back into that same transcript — regardless of which of the 13 MCP tools is
involved. Every collaborator (identity, auth, MCP, intent_service, and
conversation_history_service itself) is mocked here; conversation_history_
service's own read/write/reconstruction logic is covered separately in
test_conversation_history_service.py.

These tests deliberately mock intent_service.select_tool() rather than a
real OpenAI call — genuine LLM reasoning can't be unit-tested, and isn't the
point here. The point is: whatever the LLM decides given the history it was
shown, the orchestrator must execute and record it correctly, uniformly,
with no capability-specific branch anywhere in this pipeline."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import intent_service, message_processor


def _mock_common(monkeypatch, *, pending_state=None, history=None):
    monkeypatch.setattr(
        message_processor.conversation_state_service, "get_active_state", AsyncMock(return_value=pending_state)
    )
    merchant = MagicMock(merchant_id="mer_1")
    monkeypatch.setattr(message_processor.identity_service, "resolve_merchant", AsyncMock(return_value=merchant))
    monkeypatch.setattr(message_processor.merchant_auth_service, "get_jwt", AsyncMock(return_value="fake.jwt"))
    monkeypatch.setattr(
        message_processor.conversation_history_service, "get_recent_messages", AsyncMock(return_value=history or [])
    )
    monkeypatch.setattr(message_processor.conversation_history_service, "record_user_message", AsyncMock())
    monkeypatch.setattr(message_processor.conversation_history_service, "record_assistant_reply", AsyncMock())
    monkeypatch.setattr(message_processor.conversation_history_service, "record_tool_exchange", AsyncMock())
    return merchant


@pytest.mark.asyncio
async def test_multi_turn_completion_reverse_order_across_two_messages(monkeypatch):
    # ---- Turn 1: incomplete request, LLM asks a clarifying question ----
    _mock_common(monkeypatch, history=[])
    monkeypatch.setattr(
        message_processor.intent_service,
        "select_tool",
        AsyncMock(
            return_value=intent_service.ToolSelection(
                tool_name=None, arguments={}, reply_text="Who should I create it for?"
            )
        ),
    )

    turn1 = await message_processor._process(MagicMock(), "+919876543210", "create a payment link of 5000rs", "test")

    assert turn1.outcome == "no_tool"
    assert turn1.reply == "Who should I create it for?"
    message_processor.intent_service.select_tool.assert_awaited_once_with(
        "create a payment link of 5000rs", history=[]
    )
    message_processor.conversation_history_service.record_user_message.assert_awaited_once()
    message_processor.conversation_history_service.record_assistant_reply.assert_awaited_once()
    assert (
        message_processor.conversation_history_service.record_assistant_reply.call_args.args[2]
        == "Who should I create it for?"
    )

    # ---- Turn 2: the reply supplies the missing piece; the real recent
    # transcript (fabricated here exactly as conversation_history_service
    # would have reconstructed it) is what lets the LLM treat "for Neha
    # Pawar" as a continuation rather than an isolated fragment. ----
    prior_turn_history = [
        {"role": "user", "content": "create a payment link of 5000rs"},
        {"role": "assistant", "content": "Who should I create it for?"},
    ]
    _mock_common(monkeypatch, history=prior_turn_history)
    monkeypatch.setattr(
        message_processor.intent_service,
        "select_tool",
        AsyncMock(
            return_value=intent_service.ToolSelection(
                tool_name="create_payment_link",
                arguments={"customer_name": "Neha Pawar", "amount": 5000},
                reply_text=None,
                tool_call_id="call_1",
            )
        ),
    )
    monkeypatch.setattr(
        message_processor.mcp_client,
        "call_tool",
        AsyncMock(return_value={"success": True, "payment_link": {"amount": 5000, "shortUrl": "http://x/plink"}}),
    )

    turn2 = await message_processor._process(MagicMock(), "+919876543210", "create it for neha pawar", "test")

    assert turn2.outcome == "success"
    assert "http://x/plink" in turn2.reply
    message_processor.intent_service.select_tool.assert_awaited_once_with(
        "create it for neha pawar", history=prior_turn_history
    )
    recorded_call = message_processor.conversation_history_service.record_tool_exchange.call_args.args
    # create_payment_link is idempotency-keyed, so the recorded arguments carry
    # a server-generated idempotency_key alongside what the LLM actually chose.
    assert recorded_call[1] == "+919876543210"
    assert recorded_call[2] == "call_1"
    assert recorded_call[3] == "create_payment_link"
    assert recorded_call[4]["customer_name"] == "Neha Pawar"
    assert recorded_call[4]["amount"] == 5000
    assert recorded_call[5] == {"success": True, "payment_link": {"amount": 5000, "shortUrl": "http://x/plink"}}
    message_processor.conversation_history_service.record_assistant_reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_correction_after_completed_exchange_calls_tool_again_with_updated_amount(monkeypatch):
    """A completed create_payment_link exchange is already in history; the
    new message is a correction. The orchestrator doesn't interpret
    "actually make it 7000" itself — it just executes whatever tool call
    intent_service (given that history) decides on, which proves nothing
    here special-cases the word "actually" or any other trigger phrase."""
    history_with_prior_link = [
        {"role": "user", "content": "create a payment link for neha for 5000"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "create_payment_link",
                        "arguments": '{"customer_name": "Neha Pawar", "amount": 5000}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"success": true, "payment_link": {"amount": 5000}}'},
        {"role": "assistant", "content": "Payment link created for ₹5,000."},
    ]
    _mock_common(monkeypatch, history=history_with_prior_link)
    monkeypatch.setattr(
        message_processor.intent_service,
        "select_tool",
        AsyncMock(
            return_value=intent_service.ToolSelection(
                tool_name="create_payment_link",
                arguments={"customer_name": "Neha Pawar", "amount": 7000},
                reply_text=None,
                tool_call_id="call_2",
            )
        ),
    )
    monkeypatch.setattr(
        message_processor.mcp_client,
        "call_tool",
        AsyncMock(return_value={"success": True, "payment_link": {"amount": 7000, "shortUrl": "http://x/plink2"}}),
    )

    result = await message_processor._process(MagicMock(), "+919876543210", "Actually make it ₹7000.", "test")

    assert result.outcome == "success"
    assert "7,000" in result.reply
    message_processor.intent_service.select_tool.assert_awaited_once_with(
        "Actually make it ₹7000.", history=history_with_prior_link
    )


@pytest.mark.asyncio
async def test_task_switching_mid_clarification_executes_the_new_tool_not_the_pending_one(monkeypatch):
    """An unfinished create_payment_link clarification is in history; the
    user abandons it. The orchestrator must execute whatever tool
    intent_service selects (get_pending_payments here) — there is no code
    path that forces continuation of the previously-pending capability."""
    history_with_pending_question = [
        {"role": "user", "content": "create a payment link for neha"},
        {"role": "assistant", "content": "What amount?"},
    ]
    _mock_common(monkeypatch, history=history_with_pending_question)
    monkeypatch.setattr(
        message_processor.intent_service,
        "select_tool",
        AsyncMock(
            return_value=intent_service.ToolSelection(
                tool_name="get_pending_payments", arguments={}, reply_text=None, tool_call_id="call_3"
            )
        ),
    )
    monkeypatch.setattr(
        message_processor.mcp_client,
        "call_tool",
        AsyncMock(return_value={"success": True, "items": []}),
    )

    result = await message_processor._process(
        MagicMock(), "+919876543210", "show me my pending payments instead", "test"
    )

    assert result.outcome == "success"
    assert result.tool_name == "get_pending_payments"
    call_args = message_processor.conversation_history_service.record_tool_exchange.call_args.args
    assert call_args[3] == "get_pending_payments"  # the tool actually executed, not create_payment_link


@pytest.mark.asyncio
async def test_natural_confirmation_proceeds_using_already_established_details(monkeypatch):
    """"Yes, do it" carries no information on its own — the orchestrator
    doesn't special-case the word "yes"; it just executes whatever tool call
    intent_service produces once it has reasoned over the proposal already
    sitting in history."""
    history_with_proposal = [
        {"role": "user", "content": "create a payment link for neha for 5000"},
        {"role": "assistant", "content": "I can create a ₹5,000 payment link for Neha Pawar. Shall I proceed?"},
    ]
    _mock_common(monkeypatch, history=history_with_proposal)
    monkeypatch.setattr(
        message_processor.intent_service,
        "select_tool",
        AsyncMock(
            return_value=intent_service.ToolSelection(
                tool_name="create_payment_link",
                arguments={"customer_name": "Neha Pawar", "amount": 5000},
                reply_text=None,
                tool_call_id="call_4",
            )
        ),
    )
    monkeypatch.setattr(
        message_processor.mcp_client,
        "call_tool",
        AsyncMock(return_value={"success": True, "payment_link": {"amount": 5000, "shortUrl": "http://x/plink"}}),
    )

    result = await message_processor._process(MagicMock(), "+919876543210", "Yes, do it.", "test")

    assert result.outcome == "success"
    message_processor.intent_service.select_tool.assert_awaited_once_with("Yes, do it.", history=history_with_proposal)


@pytest.mark.asyncio
async def test_one_shot_request_still_calls_the_tool_immediately(monkeypatch):
    """A complete request must not be forced through a clarification
    round-trip just because the history-threading machinery now exists."""
    _mock_common(monkeypatch, history=[])
    monkeypatch.setattr(
        message_processor.intent_service,
        "select_tool",
        AsyncMock(
            return_value=intent_service.ToolSelection(
                tool_name="create_payment_link",
                arguments={"customer_name": "Neha Pawar", "amount": 5000},
                reply_text=None,
                tool_call_id="call_5",
            )
        ),
    )
    monkeypatch.setattr(
        message_processor.mcp_client,
        "call_tool",
        AsyncMock(return_value={"success": True, "payment_link": {"amount": 5000, "shortUrl": "http://x/plink"}}),
    )

    result = await message_processor._process(
        MagicMock(), "+919876543210", "Create a ₹5000 payment link for Neha Pawar.", "test"
    )

    assert result.outcome == "success"
    message_processor.conversation_history_service.record_tool_exchange.assert_awaited_once()


@pytest.mark.asyncio
async def test_second_capability_send_payment_reminder_also_gets_multi_turn_completion(monkeypatch):
    """Same reverse-order multi-turn completion as the payment-link test,
    but for an entirely different tool — proving the architecture is
    generic rather than wired specifically to create_payment_link."""
    _mock_common(monkeypatch, history=[])
    monkeypatch.setattr(
        message_processor.intent_service,
        "select_tool",
        AsyncMock(
            return_value=intent_service.ToolSelection(
                tool_name=None, arguments={}, reply_text="Which payment should I remind them about?"
            )
        ),
    )

    turn1 = await message_processor._process(MagicMock(), "+919876543210", "send a payment reminder", "test")
    assert turn1.outcome == "no_tool"

    prior_turn_history = [
        {"role": "user", "content": "send a payment reminder"},
        {"role": "assistant", "content": "Which payment should I remind them about?"},
    ]
    _mock_common(monkeypatch, history=prior_turn_history)
    monkeypatch.setattr(
        message_processor.intent_service,
        "select_tool",
        AsyncMock(
            return_value=intent_service.ToolSelection(
                tool_name="send_payment_reminder",
                arguments={"payment_id": "pay_123"},
                reply_text=None,
                tool_call_id="call_6",
            )
        ),
    )
    monkeypatch.setattr(
        message_processor.mcp_client,
        "call_tool",
        AsyncMock(return_value={"success": True, "reminder": {"paymentId": "pay_123", "customerId": "cus_1"}}),
    )
    monkeypatch.setattr(
        message_processor.payment_recovery_notifier,
        "notify_customer_and_watch_for_payment",
        AsyncMock(return_value=False),
    )

    turn2 = await message_processor._process(MagicMock(), "+919876543210", "for pay_123", "test")

    assert turn2.outcome == "success"
    assert turn2.tool_name == "send_payment_reminder"
    message_processor.intent_service.select_tool.assert_awaited_once_with("for pay_123", history=prior_turn_history)


@pytest.mark.asyncio
async def test_ambiguity_resolution_records_transcript_with_a_local_tool_call_id(monkeypatch):
    """The deterministic candidate-index path (_resolve_pending_state) makes
    no real OpenAI call, so it must synthesize its own tool_call_id when
    recording the exchange — the transcript still needs one so a later
    correction has a coherent history to reason over."""
    candidates = [
        {"customerId": "cus_1", "name": "Neha Pawar", "phone": "+911111111111"},
        {"customerId": "cus_2", "name": "Neha Pawar", "phone": "+912222222222"},
    ]
    state = MagicMock(
        payload={
            "tool_name": "create_payment_link",
            "original_arguments": {"amount": 5000},
            "candidates": candidates,
            "attempts": 0,
        }
    )
    monkeypatch.setattr(message_processor.conversation_state_service, "parse_candidate_index", lambda msg, n: 1)
    monkeypatch.setattr(message_processor.conversation_state_service, "resolve_state", AsyncMock())
    merchant = MagicMock(merchant_id="mer_1")
    monkeypatch.setattr(message_processor.identity_service, "resolve_merchant", AsyncMock(return_value=merchant))
    monkeypatch.setattr(message_processor.merchant_auth_service, "get_jwt", AsyncMock(return_value="fake.jwt"))
    monkeypatch.setattr(message_processor.conversation_history_service, "record_user_message", AsyncMock())
    monkeypatch.setattr(message_processor.conversation_history_service, "record_assistant_reply", AsyncMock())
    monkeypatch.setattr(message_processor.conversation_history_service, "record_tool_exchange", AsyncMock())
    monkeypatch.setattr(
        message_processor.mcp_client,
        "call_tool",
        AsyncMock(return_value={"success": True, "payment_link": {"amount": 5000, "shortUrl": "http://x/plink"}}),
    )

    result = await message_processor._resolve_pending_state(MagicMock(), "+919876543210", state, "2", "test")

    assert result.outcome == "success"
    message_processor.conversation_history_service.record_user_message.assert_awaited_once()
    tool_call_id_used = message_processor.conversation_history_service.record_tool_exchange.call_args.args[2]
    assert tool_call_id_used.startswith("local:")
