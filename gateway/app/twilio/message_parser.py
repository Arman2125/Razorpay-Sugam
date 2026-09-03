"""Parses Twilio's inbound WhatsApp webhook — form-encoded fields (From, To,
Body, MessageSid, AccountSid, NumMedia/MediaUrl0/MediaContentType0), never
Meta's JSON entry/changes/value/messages shape. Strips the "whatsapp:"
scheme Twilio prefixes onto WhatsApp addresses so the number handed to the
common pipeline is a plain phone number, exactly like every other channel's
identifier.

Twilio always sends a Body field even for a media-only message (empty
string, not absent) — so an empty Body plus NumMedia>0 is a normal voice/
video message, not a malformed request. Only a genuinely absent Body key
is treated as malformed."""

import dataclasses
from typing import Mapping, Optional


@dataclasses.dataclass
class TwilioInboundMessage:
    from_number: str  # "whatsapp:" prefix stripped, e.g. "+919876543210"
    body: str
    message_sid: str
    account_sid: str
    media_url: Optional[str] = None
    media_content_type: Optional[str] = None


def _strip_whatsapp_prefix(value: str) -> str:
    return value[len("whatsapp:"):] if value.startswith("whatsapp:") else value


def parse_inbound(form: Mapping[str, str]) -> Optional[TwilioInboundMessage]:
    from_raw = form.get("From")
    message_sid = form.get("MessageSid")
    body = form.get("Body")

    if not from_raw or not message_sid or body is None:
        return None

    try:
        has_media = int(form.get("NumMedia", "0") or "0") > 0
    except ValueError:
        has_media = False

    return TwilioInboundMessage(
        from_number=_strip_whatsapp_prefix(from_raw),
        body=body,
        message_sid=message_sid,
        account_sid=form.get("AccountSid", ""),
        media_url=form.get("MediaUrl0") or None if has_media else None,
        media_content_type=form.get("MediaContentType0") or None if has_media else None,
    )
