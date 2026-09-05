"""Covers the orchestrator behavior added for the refund/order/invoice/
subscription MCP tools: idempotency-key generation for the new financial
writes, deterministic ambiguous-customer resolution for the new
name-resolving creates (mirroring the existing create_payment_link
coverage), and that the new domains' known error codes get a friendly
formatted reply instead of falling through to the generic LLM fallback."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import intent_service, message_processor


def _mock_common(monkeypatch, *, history=None):
    monkeypatch.setattr(message_processor.conversation_state_service, "get_active_state", AsyncMock(return_value=None))
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
@pytest.mark.parametrize(
    "tool_name, arguments",
    [
        ("create_refund", {"payment_id": "pay_1", "amount": 500}),
        ("create_order", {"customer_id": "cust_1", "amount": 2500}),
        ("create_invoice", {"customer_id": "cust_1", "amount": 1000}),
        ("create_subscription", {"customer_id": "cust_1", "amount": 500, "interval": "month"}),
    ],
)
async def test_idempotency_key_generated_for_new_financial_writes(monkeypatch, tool_name, arguments):
    _mock_common(monkeypatch)
    monkeypatch.setattr(
        message_processor.intent_service,
        "select_tool",
        AsyncMock(
            return_value=intent_service.ToolSelection(
                tool_name=tool_name, arguments=dict(arguments), reply_text=None, tool_call_id="call_1"
            )
        ),
    )
    mock_call_tool = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(message_processor.mcp_client, "call_tool", mock_call_tool)

    await message_processor._process(MagicMock(), "+919876543210", "do the thing", "test")

    called_arguments = mock_call_tool.call_args.args[1]
    assert called_arguments["idempotency_key"].startswith("wa:+919876543210:")
    for key, value in arguments.items():
        assert called_arguments[key] == value


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["create_order", "create_invoice", "create_subscription"])
async def test_ambiguous_customer_resolution_merges_chosen_candidate(monkeypatch, tool_name):
    """Mirrors the existing create_payment_link ambiguity-resolution
    contract: the candidate the user picks becomes an exact customer_id, and
    customer_name is dropped so the retried call can never re-trigger the
    same ambiguity."""
    candidates = [
        {"customerId": "cus_1", "name": "Neha Pawar", "phone": "+911111111111"},
        {"customerId": "cus_2", "name": "Neha Pawar", "phone": "+912222222222"},
    ]
    state = MagicMock(
        payload={
            "tool_name": tool_name,
            "original_arguments": {"customer_name": "Neha Pawar", "amount": 5000},
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
    mock_call_tool = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(message_processor.mcp_client, "call_tool", mock_call_tool)

    result = await message_processor._resolve_pending_state(MagicMock(), "+919876543210", state, "2", "test")

    assert result.outcome == "success"
    called_arguments = mock_call_tool.call_args.args[1]
    assert called_arguments["customer_id"] == "cus_2"
    assert "customer_name" not in called_arguments
    assert called_arguments["amount"] == 5000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code, expected_substring",
    [
        ("REFUND_EXCEEDS_BALANCE", "can't refund"),
        ("PAYMENT_NOT_PAID", "isn't in a paid state"),
        ("INVOICE_NOT_DRAFT", "couldn't edit"),
        ("SUBSCRIPTION_NOT_FOUND", "couldn't find a subscription"),
        ("ORDER_NOT_FOUND", "couldn't find an order"),
        ("MISSING_CUSTOMER_IDENTIFIER", "which customer"),
        ("ALREADY_PAID", "already been paid"),
    ],
)
async def test_new_domain_error_codes_are_formatted_not_fallback(monkeypatch, code, expected_substring):
    mock_fallback = AsyncMock(return_value="Please try again shortly.")
    monkeypatch.setattr(message_processor.llm_fallback_formatter, "generate_fallback_reply", mock_fallback)

    result = await message_processor._handle_tool_result(
        MagicMock(), "+919876543210", "create_refund", {}, {"error": True, "code": code, "message": "backend detail"}
    )

    assert result.outcome == "error"
    assert result.error_code == code
    assert expected_substring in result.reply.lower()
    mock_fallback.assert_not_called()


@pytest.mark.asyncio
async def test_process_due_subscriptions_success_reply_never_claims_background_scheduling():
    result = await message_processor._handle_tool_result(
        MagicMock(),
        "+919876543210",
        "process_due_subscriptions",
        {},
        {"success": True, "processed": 1, "results": [{"subscriptionId": "sub_1", "billed": True}]},
    )
    assert result.outcome == "success"
    assert "scheduler" not in result.reply.lower()
    assert "automatically" not in result.reply.lower()
