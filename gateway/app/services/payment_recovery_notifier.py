"""
Closes the loop Mini-Razorpay itself can't be modified to support: after a
reminder is sent, this (a) generates a real payment link tied to that exact
pending payment via the existing MCP tools and messages the CUSTOMER
directly on WhatsApp with it, and (b) registers a watch row so that once
Mini-Razorpay later shows that payment as paid, the MERCHANT gets an
automatic WhatsApp confirmation. Mini-Razorpay has no webhook back into this
gateway — that's an explicit constraint, not an oversight — so (b) is done
by periodically polling the same existing get_payment_status tool every
other read already uses, never by adding anything to Mini-Razorpay itself.

Both halves are best-effort side effects of a successful reminder, not a
precondition for it: every failure here is caught and logged, never raised,
so the merchant's own "Reminder sent." confirmation is never put at risk by
this module. Nothing here is customer/merchant-specific — it's driven
entirely by whichever payment/customer/merchant the reminder was actually
for.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.mcp import mini_razorpay_mcp_client as mcp_client
from app.models import MerchantDirectoryEntry, PaymentNotificationWatch
from app.services import merchant_auth_service
from app.twilio.client import send_text_message as _send_twilio_message
from app.whatsapp.client import send_text_message as _send_meta_message

logger = logging.getLogger(__name__)


async def _send_on_channel(channel: str, to: str, body: str) -> None:
    """Reuses the exact same per-channel outbound clients every merchant
    reply already goes through — the only difference here is the
    recipient. Meta expects a bare digit string (no "+"), unlike Twilio's
    "whatsapp:+E164"; Mini-Razorpay's Customer.phone is stored with a "+",
    so it's normalized per-channel here, not upstream."""
    if channel == "twilio":
        await _send_twilio_message(to, body)
    elif channel == "whatsapp":
        await _send_meta_message(to.lstrip("+"), body)
    else:
        logger.info("[%s channel has no real outbound send] Would message %s: %s", channel, to, body)


async def notify_customer_and_watch_for_payment(
    session,
    *,
    merchant_id: str,
    merchant_whatsapp_number: str,
    channel: str,
    token: str,
    payment_id: str,
    customer_id: str,
) -> bool:
    """Best-effort: look up the customer, create a real payment link for
    this exact pending payment (never a fresh ad-hoc amount), message them
    directly, and start watching for it to be paid. Returns True only if
    the customer was actually messaged — used solely to slightly enrich the
    merchant's own reply text, never required for the reminder itself to
    count as successful."""
    try:
        customer_result = await mcp_client.call_tool("get_customer", {"customer_id": customer_id}, token)
        if not customer_result.get("success"):
            logger.warning("Could not look up customer %s to notify them directly: %s", customer_id, customer_result)
            return False
        customer = customer_result["customer"]

        link_result = await mcp_client.call_tool(
            "create_payment_link",
            {"customer_id": customer_id, "existing_payment_id": payment_id},
            token,
        )
        if not link_result.get("success"):
            logger.warning("Could not create a payment link for payment %s: %s", payment_id, link_result)
            return False
        link = link_result["payment_link"]

        message = (
            f"Hi {customer.get('name', 'there')}, this is a payment reminder. "
            f"Your payment of ₹{link['amount']:,.0f} is pending. "
            f"Pay now: {link['shortUrl']}"
        )
        await _send_on_channel(channel, customer["phone"], message)

        existing_watch = await session.scalar(
            select(PaymentNotificationWatch).where(
                PaymentNotificationWatch.merchant_id == merchant_id,
                PaymentNotificationWatch.payment_id == payment_id,
                PaymentNotificationWatch.status == "watching",
            )
        )
        if existing_watch is None:
            session.add(
                PaymentNotificationWatch(
                    merchant_id=merchant_id,
                    payment_id=payment_id,
                    customer_id=customer_id,
                    customer_name=customer.get("name", "Customer"),
                    amount=link["amount"],
                    channel=channel,
                    merchant_whatsapp_number=merchant_whatsapp_number,
                )
            )
            await session.commit()

        return True
    except Exception:
        logger.exception("Failed to notify customer %s about payment %s", customer_id, payment_id)
        return False


async def poll_once(session) -> None:
    """One polling pass: check every currently-watched payment, and confirm
    to the merchant any that have become paid since the last pass."""
    watches = (
        await session.scalars(select(PaymentNotificationWatch).where(PaymentNotificationWatch.status == "watching"))
    ).all()

    for watch in watches:
        try:
            merchant = await session.scalar(
                select(MerchantDirectoryEntry).where(MerchantDirectoryEntry.merchant_id == watch.merchant_id)
            )
            if merchant is None:
                continue

            token = await merchant_auth_service.get_jwt(session, merchant)
            status_result = await mcp_client.call_tool("get_payment_status", {"payment_id": watch.payment_id}, token)
            if not status_result.get("success") or status_result["payment"].get("status") != "paid":
                continue

            message = (
                f"{watch.customer_name} has paid ₹{watch.amount:,.0f} "
                f"(payment {watch.payment_id}). Recovered ✓"
            )
            await _send_on_channel(watch.channel, watch.merchant_whatsapp_number, message)

            watch.status = "notified"
            watch.notified_at = datetime.now(timezone.utc)
            await session.commit()
        except Exception:
            logger.exception("Failed checking/notifying payment watch %s", watch.id)


async def run_periodic(session_factory, interval_seconds: int) -> None:
    while True:
        try:
            async with session_factory() as session:
                await poll_once(session)
        except Exception:
            logger.exception("Payment recovery poll failed — will retry next interval")
        await asyncio.sleep(interval_seconds)
