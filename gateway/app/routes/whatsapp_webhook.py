"""
Meta WhatsApp Cloud API webhook — GET for the verification challenge, POST
for incoming messages. Code-complete and mounted regardless of
WHATSAPP_ENABLED_RAW (so the route exists and can be pointed at from Meta's
app dashboard whenever real credentials are ready), but every POST body's
authenticity is still checked via HMAC before anything runs, and outbound
replies go through whatsapp/client.py, which itself short-circuits until
enabled.
"""

import logging

from fastapi import APIRouter, Request, Response

from app.config import settings
from app.services.message_processor import process_user_message
from app.whatsapp.client import mark_message_as_read, send_text_message
from app.whatsapp.webhook_security import verify_signature

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/webhook/whatsapp")
async def verify_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == settings.whatsapp_verify_token:
        return Response(content=challenge or "", media_type="text/plain")
    return Response(status_code=403)


@router.post("/webhook/whatsapp")
async def receive_webhook(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")

    if not verify_signature(raw_body, signature, settings.whatsapp_app_secret):
        logger.warning("Rejected WhatsApp webhook — invalid signature")
        return Response(status_code=403)

    payload = await request.json()

    try:
        entry = payload["entry"][0]
        change = entry["changes"][0]["value"]
        messages = change.get("messages", [])
    except (KeyError, IndexError):
        # Meta also posts status callbacks (delivered/read) with no
        # "messages" key — always 200 those, never treat as an error.
        return Response(status_code=200)

    for msg in messages:
        from_number = msg.get("from")
        text = (msg.get("text") or {}).get("body", "")
        if not from_number or not text:
            continue

        message_id = msg.get("id")
        if message_id:
            await mark_message_as_read(message_id)

        result = await process_user_message(from_number, text, channel="whatsapp")
        await send_text_message(from_number, result.reply)

    return Response(status_code=200)
