"""Verifies Twilio's X-Twilio-Signature header using Twilio's own
RequestValidator — HMAC-SHA1 over the exact webhook URL plus the sorted
form parameters, keyed by the Twilio Auth Token. Deliberately separate from
Meta's X-Hub-Signature-256/HMAC-SHA256 check in
app/whatsapp/webhook_security.py: the two providers use different schemes
over different inputs (URL+form vs. raw JSON body) and must never share
validation logic."""

from typing import Optional

from twilio.request_validator import RequestValidator


def verify_signature(url: str, params: dict, signature_header: Optional[str], auth_token: str) -> bool:
    if not signature_header or not auth_token:
        return False
    return RequestValidator(auth_token).validate(url, params, signature_header)
