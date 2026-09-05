"""Sends outbound WhatsApp messages via Meta's Graph API. Short-circuits
(logs, doesn't attempt a real call) until WHATSAPP_ENABLED_RAW parses true
AND real credentials are set — safe to import and call in any environment,
including this one, before real Meta credentials exist."""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def send_text_message(to: str, body: str) -> None:
    if not settings.whatsapp_enabled:
        logger.info("[WHATSAPP DISABLED] Would send to %s: %s", to, body)
        return

    if not (settings.whatsapp_access_token and settings.whatsapp_phone_number_id):
        logger.warning("WHATSAPP_ENABLED_RAW is true but credentials are missing — not sending")
        return

    url = (
        f"https://graph.facebook.com/{settings.whatsapp_graph_api_version}/"
        f"{settings.whatsapp_phone_number_id}/messages"
    )
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            logger.error("WhatsApp send failed (%s): %s", response.status_code, response.text[:300])


async def mark_message_as_read(message_id: str) -> None:
    """Marks an inbound message as read (blue double-check on the sender's
    phone) and shows the typing indicator while a reply is being prepared.
    Same enabled/credentials short-circuit as send_text_message."""
    if not settings.whatsapp_enabled:
        logger.info("[WHATSAPP DISABLED] Would mark as read: %s", message_id)
        return

    if not (settings.whatsapp_access_token and settings.whatsapp_phone_number_id):
        logger.warning("WHATSAPP_ENABLED_RAW is true but credentials are missing — not marking read")
        return

    url = (
        f"https://graph.facebook.com/{settings.whatsapp_graph_api_version}/"
        f"{settings.whatsapp_phone_number_id}/messages"
    )
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {"type": "text"},
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            logger.error("WhatsApp mark-as-read failed (%s): %s", response.status_code, response.text[:300])
