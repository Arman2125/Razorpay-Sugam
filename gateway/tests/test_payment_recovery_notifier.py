"""payment_recovery_notifier.py is the piece that closes the loop Mini-Razorpay
itself can't be modified to support: messaging the customer directly after a
reminder, and polling for the merchant confirmation once paid. Every
external call (MCP, Meta) is mocked — none of this needs live
credentials or a live database to verify."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import payment_recovery_notifier as notifier


def _customer_result(name="Rahul Sharma", phone="+919820011111"):
    return {"success": True, "customer": {"name": name, "phone": phone}}


def _link_result(amount=25000, short_url="http://localhost:5000/pay/plink_abc"):
    return {"success": True, "payment_link": {"amount": amount, "shortUrl": short_url}}


@pytest.mark.asyncio
async def test_successful_notify_messages_customer_and_creates_watch(monkeypatch):
    mock_call_tool = AsyncMock(side_effect=[_customer_result(), _link_result()])
    monkeypatch.setattr(notifier.mcp_client, "call_tool", mock_call_tool)
    mock_send = AsyncMock()
    monkeypatch.setattr(notifier, "_send_on_channel", mock_send)

    session = MagicMock()
    session.scalar = AsyncMock(return_value=None)  # no existing watch
    session.add = MagicMock()
    session.commit = AsyncMock()

    result = await notifier.notify_customer_and_watch_for_payment(
        session,
        merchant_id="mer_1",
        merchant_whatsapp_number="+919876543210",
        channel="twilio",
        token="fake.jwt",
        payment_id="pay_123",
        customer_id="cus_456",
    )

    assert result is True
    mock_send.assert_awaited_once()
    sent_channel, sent_to, sent_body = mock_send.await_args.args
    assert sent_channel == "twilio"
    assert sent_to == "+919820011111"
    assert "₹25,000" in sent_body
    assert "http://localhost:5000/pay/plink_abc" in sent_body
    session.add.assert_called_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_customer_failure_never_sends_or_watches(monkeypatch):
    monkeypatch.setattr(notifier.mcp_client, "call_tool", AsyncMock(return_value={"success": False}))
    mock_send = AsyncMock()
    monkeypatch.setattr(notifier, "_send_on_channel", mock_send)
    session = MagicMock()
    session.add = MagicMock()

    result = await notifier.notify_customer_and_watch_for_payment(
        session,
        merchant_id="mer_1",
        merchant_whatsapp_number="+919876543210",
        channel="twilio",
        token="fake.jwt",
        payment_id="pay_123",
        customer_id="cus_456",
    )

    assert result is False
    mock_send.assert_not_called()
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_create_payment_link_failure_never_sends_or_watches(monkeypatch):
    mock_call_tool = AsyncMock(side_effect=[_customer_result(), {"success": False, "error": True}])
    monkeypatch.setattr(notifier.mcp_client, "call_tool", mock_call_tool)
    mock_send = AsyncMock()
    monkeypatch.setattr(notifier, "_send_on_channel", mock_send)
    session = MagicMock()
    session.add = MagicMock()

    result = await notifier.notify_customer_and_watch_for_payment(
        session,
        merchant_id="mer_1",
        merchant_whatsapp_number="+919876543210",
        channel="twilio",
        token="fake.jwt",
        payment_id="pay_123",
        customer_id="cus_456",
    )

    assert result is False
    mock_send.assert_not_called()
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_existing_active_watch_is_not_duplicated_but_customer_is_still_messaged(monkeypatch):
    mock_call_tool = AsyncMock(side_effect=[_customer_result(), _link_result()])
    monkeypatch.setattr(notifier.mcp_client, "call_tool", mock_call_tool)
    mock_send = AsyncMock()
    monkeypatch.setattr(notifier, "_send_on_channel", mock_send)

    session = MagicMock()
    session.scalar = AsyncMock(return_value=MagicMock())  # an active watch already exists
    session.add = MagicMock()
    session.commit = AsyncMock()

    result = await notifier.notify_customer_and_watch_for_payment(
        session,
        merchant_id="mer_1",
        merchant_whatsapp_number="+919876543210",
        channel="twilio",
        token="fake.jwt",
        payment_id="pay_123",
        customer_id="cus_456",
    )

    assert result is True
    mock_send.assert_awaited_once()
    session.add.assert_not_called()  # no duplicate watch row


@pytest.mark.asyncio
async def test_unexpected_exception_is_caught_and_returns_false(monkeypatch):
    monkeypatch.setattr(notifier.mcp_client, "call_tool", AsyncMock(side_effect=RuntimeError("MCP subprocess crashed")))
    session = MagicMock()

    result = await notifier.notify_customer_and_watch_for_payment(
        session,
        merchant_id="mer_1",
        merchant_whatsapp_number="+919876543210",
        channel="twilio",
        token="fake.jwt",
        payment_id="pay_123",
        customer_id="cus_456",
    )

    assert result is False


@pytest.mark.asyncio
async def test_send_on_channel_routes_meta_and_strips_plus(monkeypatch):
    mock_meta = AsyncMock()
    monkeypatch.setattr(notifier, "_send_meta_message", mock_meta)

    await notifier._send_on_channel("whatsapp", "+919820011111", "hello")

    mock_meta.assert_awaited_once_with("919820011111", "hello")


@pytest.mark.asyncio
async def test_send_on_channel_test_channel_is_a_safe_noop(monkeypatch):
    mock_meta = AsyncMock()
    monkeypatch.setattr(notifier, "_send_meta_message", mock_meta)

    await notifier._send_on_channel("test", "+919820011111", "hello")  # must not raise

    mock_meta.assert_not_called()


@pytest.mark.asyncio
async def test_poll_once_notifies_merchant_when_paid_and_marks_watch_notified(monkeypatch):
    watch = MagicMock(
        id=1,
        merchant_id="mer_1",
        payment_id="pay_123",
        customer_name="Rahul Sharma",
        amount=25000,
        channel="twilio",
        merchant_whatsapp_number="+919876543210",
        status="watching",
    )
    session = MagicMock()
    session.scalars = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[watch])))
    fake_merchant = MagicMock(merchant_id="mer_1")
    session.scalar = AsyncMock(return_value=fake_merchant)
    session.commit = AsyncMock()

    monkeypatch.setattr(notifier.merchant_auth_service, "get_jwt", AsyncMock(return_value="fake.jwt"))
    monkeypatch.setattr(
        notifier.mcp_client, "call_tool", AsyncMock(return_value={"success": True, "payment": {"status": "paid"}})
    )
    mock_send = AsyncMock()
    monkeypatch.setattr(notifier, "_send_on_channel", mock_send)

    await notifier.poll_once(session)

    mock_send.assert_awaited_once()
    sent_channel, sent_to, sent_body = mock_send.await_args.args
    assert sent_to == "+919876543210"
    assert "Rahul Sharma" in sent_body
    assert "Recovered" in sent_body
    assert watch.status == "notified"
    assert watch.notified_at is not None
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_once_still_pending_does_not_notify(monkeypatch):
    watch = MagicMock(id=1, merchant_id="mer_1", payment_id="pay_123", status="watching")
    session = MagicMock()
    session.scalars = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[watch])))
    session.scalar = AsyncMock(return_value=MagicMock(merchant_id="mer_1"))

    monkeypatch.setattr(notifier.merchant_auth_service, "get_jwt", AsyncMock(return_value="fake.jwt"))
    monkeypatch.setattr(
        notifier.mcp_client, "call_tool", AsyncMock(return_value={"success": True, "payment": {"status": "pending"}})
    )
    mock_send = AsyncMock()
    monkeypatch.setattr(notifier, "_send_on_channel", mock_send)

    await notifier.poll_once(session)

    mock_send.assert_not_called()
    assert watch.status == "watching"


@pytest.mark.asyncio
async def test_poll_once_unknown_merchant_is_skipped_safely(monkeypatch):
    watch = MagicMock(id=1, merchant_id="mer_gone", payment_id="pay_123", status="watching")
    session = MagicMock()
    session.scalars = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[watch])))
    session.scalar = AsyncMock(return_value=None)  # merchant no longer in the directory

    mock_call_tool = AsyncMock()
    monkeypatch.setattr(notifier.mcp_client, "call_tool", mock_call_tool)

    await notifier.poll_once(session)  # must not raise

    mock_call_tool.assert_not_called()


@pytest.mark.asyncio
async def test_poll_once_one_failing_watch_does_not_block_others(monkeypatch):
    broken_watch = MagicMock(id=1, merchant_id="mer_1", payment_id="pay_broken", status="watching")
    healthy_watch = MagicMock(
        id=2,
        merchant_id="mer_1",
        payment_id="pay_ok",
        customer_name="Amit Singh",
        amount=5000,
        channel="whatsapp",
        merchant_whatsapp_number="919876543210",
        status="watching",
    )
    session = MagicMock()
    session.scalars = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[broken_watch, healthy_watch])))
    session.scalar = AsyncMock(return_value=MagicMock(merchant_id="mer_1"))
    session.commit = AsyncMock()

    monkeypatch.setattr(notifier.merchant_auth_service, "get_jwt", AsyncMock(return_value="fake.jwt"))

    async def call_tool_side_effect(tool_name, args, token):
        if args["payment_id"] == "pay_broken":
            raise RuntimeError("Mini-Razorpay unreachable")
        return {"success": True, "payment": {"status": "paid"}}

    monkeypatch.setattr(notifier.mcp_client, "call_tool", AsyncMock(side_effect=call_tool_side_effect))
    mock_send = AsyncMock()
    monkeypatch.setattr(notifier, "_send_on_channel", mock_send)

    await notifier.poll_once(session)  # must not raise despite the first watch failing

    mock_send.assert_awaited_once()
    assert healthy_watch.status == "notified"
