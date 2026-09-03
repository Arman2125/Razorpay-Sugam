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
