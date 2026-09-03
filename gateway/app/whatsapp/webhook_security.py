"""Verifies Meta's X-Hub-Signature-256 header — HMAC-SHA256 of the raw
request body, keyed by the Meta app secret. Mirrors Sugam AI OS's
webhook_security.py exactly; this is a well-tested, minimal primitive with
nothing project-specific to adapt."""

import hashlib
import hmac
from typing import Optional


def verify_signature(raw_body: bytes, signature_header: Optional[str], app_secret: str) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected_signature = signature_header.split("=", 1)[1]
    computed_signature = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed_signature, expected_signature)
