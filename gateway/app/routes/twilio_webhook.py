"""
Twilio WhatsApp webhook — POST only, form-encoded (From/To/Body/MessageSid/
AccountSid/NumMedia/MediaUrl0/MediaContentType0), never Meta's JSON
entry/changes/value/messages shape. Every request's X-Twilio-Signature is
verified against TWILIO_AUTH_TOKEN before anything runs; outbound replies
go through app/twilio/client.py, which itself short-circuits until enabled.

This route is purely a transport adapter: it authenticates the request,
parses Twilio's shape into (from_number, text), and hands off to the exact
same channel-independent process_user_message() that /webhook/whatsapp
(Meta) and /test/message already use. It does not replace, alter, or share
mutable state with the Meta webhook.

Text messages go straight through, unmodified — no extra model call. A
voice note or video is first turned into plain text by Gemini
(app/services/media_understanding_service.py, via
app/twilio/media_fetcher.py for the authenticated download); that text
then takes the exact same path as typed text, so OpenAI GPT
(intent_service.py) remains the only reasoning/tool-selection step either
way. This route never selects a tool or reasons about content itself.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Request, Response

from app.config import settings
from app.services import media_understanding_service
from app.services.message_processor import process_user_message
from app.twilio import dedup, media_fetcher
from app.twilio.client import send_text_message
from app.twilio.message_parser import TwilioInboundMessage, parse_inbound
from app.twilio.webhook_security import verify_signature

logger = logging.getLogger(__name__)
router = APIRouter()

MEDIA_FAILURE_REPLY = "Sorry, I couldn't process that voice/video message. Please try again, or type your request instead."


async def _resolve_text(message: TwilioInboundMessage) -> Optional[str]:
    """Returns the plain text to reason about, or None if there's nothing
    usable — a text message uses its Body directly (no model call); a
    voice/video message is routed through Gemini first."""
    if not (message.media_url and message.media_content_type):
        return message.body or None

    if not media_understanding_service.is_supported_media(message.media_content_type):
        logger.info("Unsupported Twilio media type %s from %s", message.media_content_type, message.from_number)
        return None

    media_bytes = await media_fetcher.fetch_media(message.media_url)
    if media_bytes is None:
        return None

    return await media_understanding_service.understand_media(media_bytes, message.media_content_type)


@router.post("/webhook/twilio/whatsapp")
async def receive_twilio_webhook(request: Request):
    form = dict(await request.form())
    signature = request.headers.get("X-Twilio-Signature")
    validation_url = settings.twilio_webhook_url_override or str(request.url)

    if not verify_signature(validation_url, form, signature, settings.twilio_auth_token):
        logger.warning("Rejected Twilio webhook — invalid signature")
        return Response(status_code=403)

    message = parse_inbound(form)
    if message is None:
        # Missing From/Body/MessageSid — always 200 so Twilio doesn't retry
        # something we'll never handle.
        return Response(status_code=200)

    if dedup.seen_before(message.message_sid):
        logger.info("Duplicate Twilio MessageSid %s — not reprocessing", message.message_sid)
        return Response(status_code=200)

    is_media_message = bool(message.media_url and message.media_content_type)
    text = await _resolve_text(message)

    if text is None:
        if is_media_message:
            await send_text_message(message.from_number, MEDIA_FAILURE_REPLY)
        # A plain request with no body and no media has nothing to process.
        return Response(status_code=200)

    result = await process_user_message(message.from_number, text, channel="twilio")
    await send_text_message(message.from_number, result.reply)

    return Response(status_code=200)
