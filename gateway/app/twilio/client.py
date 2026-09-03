"""Sends outbound WhatsApp messages via Twilio's Messages API using the
official Twilio Python SDK. Short-circuits (logs, doesn't attempt a real
call) until TWILIO_ENABLED_RAW parses true AND real credentials are set —
mirrors app/whatsapp/client.py's Meta short-circuit convention exactly, so
either channel is safe to import and call in any environment, including
this one, before real Twilio credentials exist.

The Twilio SDK's Client is synchronous (backed by `requests`); it's run in
a worker thread via asyncio.to_thread so it never blocks the event loop,
the same way app/whatsapp/client.py uses an async httpx client for Meta."""

import asyncio
import logging

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from app.config import settings

logger = logging.getLogger(__name__)


def _with_whatsapp_prefix(number: str) -> str:
    return number if number.startswith("whatsapp:") else f"whatsapp:{number}"


async def send_text_message(to: str, body: str) -> None:
    if not settings.twilio_enabled:
        logger.info("[TWILIO DISABLED] Would send to %s: %s", to, body)
        return

    if not (settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_whatsapp_number):
        logger.warning("TWILIO_ENABLED_RAW is true but credentials are missing — not sending")
        return

    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    try:
        await asyncio.to_thread(
            client.messages.create,
            from_=_with_whatsapp_prefix(settings.twilio_whatsapp_number),
            to=_with_whatsapp_prefix(to),
            body=body,
        )
    except TwilioRestException as e:
        logger.error("Twilio send failed (%s): %s", e.code, e.msg)
    except Exception:
        logger.exception("Twilio send failed with an unexpected error")
