"""Fetches a Twilio media attachment's raw bytes. Twilio's MediaUrl0 (etc.)
requires HTTP Basic Auth with the account's own Account SID / Auth Token —
without it Twilio returns 401/403. Deliberately Twilio-specific (the auth
scheme and URL shape are Twilio's own); everything downstream
(app/services/media_understanding_service.py) only ever sees raw bytes plus
a MIME type, never a Twilio URL."""

import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_MAX_MEDIA_BYTES = 20 * 1024 * 1024  # Gemini's inline-request size ceiling


async def fetch_media(media_url: str) -> Optional[bytes]:
    if not media_url:
        return None

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                media_url, auth=(settings.twilio_account_sid, settings.twilio_auth_token)
            )
    except Exception:
        logger.exception("Fetching Twilio media failed")
        return None

    if response.status_code >= 400:
        logger.warning("Twilio media fetch returned %s", response.status_code)
        return None

    if len(response.content) > _MAX_MEDIA_BYTES:
        logger.warning("Twilio media exceeds %d-byte limit — skipping", _MAX_MEDIA_BYTES)
        return None

    return response.content
