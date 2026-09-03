"""
Phone number normalization — mirrors Sugam AI OS's
phone_number_utils.normalize_indian_mobile_number(): strip everything but
digits, drop a leading country-code prefix, validate the result is a real
10-digit Indian mobile number. Never raises; returns None for anything that
doesn't match.
"""

import re

_MOBILE_NUMBER_RE = re.compile(r"^[6-9]\d{9}$")


def normalize_phone(raw: str) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"[^0-9]", "", str(raw))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if _MOBILE_NUMBER_RE.match(digits) else None
