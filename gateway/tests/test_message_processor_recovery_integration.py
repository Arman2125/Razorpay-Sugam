"""Verifies message_processor wires a successful send_payment_reminder tool
result into payment_recovery_notifier correctly — right arguments, right
channel, and the merchant's reply reflects whether the customer was
actually messaged. Does not touch payment_recovery_notifier's own internals
(covered separately in test_payment_recovery_notifier.py)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import message_processor


@pytest.mark.asyncio
async def test_successful_reminder_triggers_customer_notification_with_correct_args(monkeypatch):
    mock_notify = AsyncMock(return_value=True)
    monkeypatch.setattr(message_processor.payment_recovery_notifier, "notify_customer_and_watch_for_payment", mock_notify)

    merchant = MagicMock(merchant_id="mer_1")
    tool_result = {"success": True, "reminder": {"paymentId": "pay_123", "customerId": "cus_456"}}

    result = await message_processor._handle_tool_result(
        MagicMock(),
        "+919876543210",
        "send_payment_reminder",
        {"payment_id": "pay_123"},
        tool_result,
        channel="twilio",
        merchant=merchant,
        token="fake.jwt",
    )

    mock_notify.assert_awaited_once()
    kwargs = mock_notify.await_args.kwargs
    assert kwargs["merchant_id"] == "mer_1"
    assert kwargs["merchant_whatsapp_number"] == "+919876543210"
    assert kwargs["channel"] == "twilio"
    assert kwargs["token"] == "fake.jwt"
    assert kwargs["payment_id"] == "pay_123"
    assert kwargs["customer_id"] == "cus_456"
    assert result.outcome == "success"
    assert "messaged the customer directly" in result.reply


@pytest.mark.asyncio
async def test_reminder_reply_is_unchanged_when_customer_notification_fails(monkeypatch):
    monkeypatch.setattr(
        message_processor.payment_recovery_notifier, "notify_customer_and_watch_for_payment", AsyncMock(return_value=False)
    )
    merchant = MagicMock(merchant_id="mer_1")
    tool_result = {"success": True, "reminder": {"paymentId": "pay_123", "customerId": "cus_456"}}

    result = await message_processor._handle_tool_result(
        MagicMock(), "+919876543210", "send_payment_reminder", {}, tool_result, channel="twilio", merchant=merchant, token="fake.jwt"
    )

    assert result.reply == "Reminder sent."


@pytest.mark.asyncio
async def test_other_tools_never_trigger_customer_notification(monkeypatch):
    mock_notify = AsyncMock()
    monkeypatch.setattr(message_processor.payment_recovery_notifier, "notify_customer_and_watch_for_payment", mock_notify)
    merchant = MagicMock(merchant_id="mer_1")

    await message_processor._handle_tool_result(
        MagicMock(),
        "+919876543210",
        "get_payments_summary",
        {},
        {"success": True, "summary": {"totalPayments": 0, "totalAmount": 0, "pendingCount": 0, "pendingAmount": 0, "paidCount": 0, "paidAmount": 0, "overdueCount": 0}},
        channel="twilio",
        merchant=merchant,
        token="fake.jwt",
    )

    mock_notify.assert_not_called()


@pytest.mark.asyncio
async def test_missing_merchant_or_token_skips_notification_without_raising(monkeypatch):
    mock_notify = AsyncMock()
    monkeypatch.setattr(message_processor.payment_recovery_notifier, "notify_customer_and_watch_for_payment", mock_notify)
    tool_result = {"success": True, "reminder": {"paymentId": "pay_123", "customerId": "cus_456"}}

    # Mirrors how existing tests call _handle_tool_result directly without
    # merchant/token — must not raise now that the reminder-notification
    # branch reads merchant.merchant_id.
    result = await message_processor._handle_tool_result(MagicMock(), "+919876543210", "send_payment_reminder", {}, tool_result)

    mock_notify.assert_not_called()
    assert result.outcome == "success"
    assert result.reply == "Reminder sent."
