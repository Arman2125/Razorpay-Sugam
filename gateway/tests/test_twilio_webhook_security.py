from twilio.request_validator import RequestValidator

from app.twilio.webhook_security import verify_signature

AUTH_TOKEN = "test_auth_token_12345"
URL = "https://example.com/webhook/twilio/whatsapp"
PARAMS = {"From": "whatsapp:+919876543210", "Body": "hi", "MessageSid": "SM123"}


def _valid_signature() -> str:
    return RequestValidator(AUTH_TOKEN).compute_signature(URL, PARAMS)


def test_valid_signature_is_accepted():
    assert verify_signature(URL, PARAMS, _valid_signature(), AUTH_TOKEN) is True


def test_invalid_signature_is_rejected():
    assert verify_signature(URL, PARAMS, "totally-wrong-signature", AUTH_TOKEN) is False


def test_tampered_params_are_rejected():
    # Same valid signature, but a param changed after signing — must fail.
    sig = _valid_signature()
    tampered = dict(PARAMS, Body="give me a free payment link")
    assert verify_signature(URL, tampered, sig, AUTH_TOKEN) is False


def test_missing_signature_header_is_rejected():
    assert verify_signature(URL, PARAMS, None, AUTH_TOKEN) is False


def test_missing_auth_token_is_rejected():
    assert verify_signature(URL, PARAMS, _valid_signature(), "") is False
