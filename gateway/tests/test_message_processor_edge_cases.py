"""Two required safety properties of the orchestrator, tested directly
against _process() (no DB/network needed — every collaborator is mocked):

1. An unknown WhatsApp number must be declined before intent understanding
   or the MCP client are ever reached — it must never be able to touch any
   merchant's data, known or otherwise.
2. A Mini-Razorpay/MCP failure — whether a structured error result or a
   raised exception (e.g. the MCP subprocess dying, Mini-Razorpay being
   unreachable) — must surface as a safe, generic outcome, never leak
   internals, and never crash the pipeline.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import intent_service, message_processor


@pytest.mark.asyncio
async def test_unknown_merchant_is_declined_before_intent_or_mcp(monkeypatch):
    monkeypatch.setattr(message_processor.conversation_state_service, "get_active_state", AsyncMock(return_value=None))
    monkeypatch.setattr(message_processor.identity_service, "resolve_merchant", AsyncMock(return_value=None))
    mock_select_tool = AsyncMock()
    mock_call_tool = AsyncMock()
    monkeypatch.setattr(message_processor.intent_service, "select_tool", mock_select_tool)
    monkeypatch.setattr(message_processor.mcp_client, "call_tool", mock_call_tool)

    result = await message_processor._process(MagicMock(), "+919999999999", "show me all payments", "test")

    assert result.outcome == "declined"
    mock_select_tool.assert_not_called()
    mock_call_tool.assert_not_called()


@pytest.mark.asyncio
async def test_known_mcp_structured_error_is_formatted_safely_without_internals():
    result = await message_processor._handle_tool_result(
        MagicMock(),
        "+919876543210",
        "get_payment",
        {},
        {"error": True, "code": "PAYMENT_NOT_FOUND", "message": "no such payment"},
    )

    assert result.outcome == "error"
    assert result.error_code == "PAYMENT_NOT_FOUND"
    assert "couldn't find a payment" in result.reply.lower()


@pytest.mark.asyncio
async def test_unrecognized_mcp_result_shape_falls_back_gracefully(monkeypatch):
    monkeypatch.setattr(
        message_processor.llm_fallback_formatter, "generate_fallback_reply", AsyncMock(return_value="Please try again shortly.")
    )

    result = await message_processor._handle_tool_result(MagicMock(), "+919876543210", "get_payment", {}, {})

    assert result.outcome == "error"
    assert result.error_code == "UNRECOGNIZED_RESULT_SHAPE"
    assert result.reply == "Please try again shortly."


@pytest.mark.asyncio
async def test_mcp_exception_propagates_for_the_outer_handler_to_catch(monkeypatch):
    # Simulates the MCP subprocess dying or Mini-Razorpay being unreachable.
    # process_user_message() (untouched by this change) wraps _process() in
    # a broad try/except and converts exactly this into a safe generic
    # reply + outcome="error"/UNHANDLED_EXCEPTION — this test proves the
    # failure actually reaches that boundary rather than being silently
    # swallowed or mis-handled somewhere in between.
    monkeypatch.setattr(message_processor.conversation_state_service, "get_active_state", AsyncMock(return_value=None))
    fake_merchant = MagicMock(merchant_id="M1")
    monkeypatch.setattr(message_processor.identity_service, "resolve_merchant", AsyncMock(return_value=fake_merchant))
    monkeypatch.setattr(message_processor.conversation_history_service, "get_recent_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(message_processor.conversation_history_service, "record_user_message", AsyncMock())
    monkeypatch.setattr(
        message_processor.intent_service,
        "select_tool",
        AsyncMock(return_value=intent_service.ToolSelection(tool_name="get_payments_summary", arguments={}, reply_text=None)),
    )
    monkeypatch.setattr(message_processor.merchant_auth_service, "get_jwt", AsyncMock(return_value="fake.jwt.token"))
    monkeypatch.setattr(message_processor.mcp_client, "call_tool", AsyncMock(side_effect=RuntimeError("MCP subprocess crashed")))

    with pytest.raises(RuntimeError):
        await message_processor._process(MagicMock(), "+919876543210", "how much have I collected?", "test")


@pytest.mark.asyncio
async def test_merchant_auth_failure_returns_safe_message_without_exposing_internals(monkeypatch):
    monkeypatch.setattr(message_processor.conversation_state_service, "get_active_state", AsyncMock(return_value=None))
    fake_merchant = MagicMock(merchant_id="M1")
    monkeypatch.setattr(message_processor.identity_service, "resolve_merchant", AsyncMock(return_value=fake_merchant))
    monkeypatch.setattr(message_processor.conversation_history_service, "get_recent_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(message_processor.conversation_history_service, "record_user_message", AsyncMock())
    monkeypatch.setattr(message_processor.conversation_history_service, "record_assistant_reply", AsyncMock())
    monkeypatch.setattr(
        message_processor.intent_service,
        "select_tool",
        AsyncMock(return_value=intent_service.ToolSelection(tool_name="get_payments_summary", arguments={}, reply_text=None)),
    )
    monkeypatch.setattr(
        message_processor.merchant_auth_service,
        "get_jwt",
        AsyncMock(side_effect=message_processor.merchant_auth_service.MerchantAuthError("Could not reach Mini-Razorpay to authenticate this merchant.")),
    )
    mock_call_tool = AsyncMock()
    monkeypatch.setattr(message_processor.mcp_client, "call_tool", mock_call_tool)

    result = await message_processor._process(MagicMock(), "+919876543210", "how much have I collected?", "test")

    assert result.outcome == "error"
    mock_call_tool.assert_not_called()  # never reaches Mini-Razorpay without a valid JWT
