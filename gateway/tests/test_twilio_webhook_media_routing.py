"""Integration tests for the text-vs-audio-vs-video branching in
POST /webhook/twilio/whatsapp. Mounted on a minimal app (see
test_twilio_webhook_route.py's approach) so no Postgres/MCP is touched.
process_user_message, send_text_message, media_fetcher.fetch_media, and
media_understanding_service.understand_media are all mocked at the route
module's import sites — this proves the actual routing decisions (does
Gemini get called? does OpenAI's pipeline get the right text?) without
needing live Twilio, OpenAI, or Gemini credentials."""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from twilio.request_validator import RequestValidator

from app.config import settings
from app.routes import twilio_webhook
from app.services.message_processor import ProcessResult

WEBHOOK_URL = "http://testserver/webhook/twilio/whatsapp"
AUTH_TOKEN = "media_route_test_token"


@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setattr(settings, "twilio_auth_token", AUTH_TOKEN)
    monkeypatch.setattr(settings, "twilio_webhook_url_override", WEBHOOK_URL)

    app = FastAPI()
    app.include_router(twilio_webhook.router)
    return TestClient(app)


def _sign(params: dict) -> str:
    return RequestValidator(AUTH_TOKEN).compute_signature(WEBHOOK_URL, params)


def _post(app_client, form: dict):
    return app_client.post(WEBHOOK_URL, data=form, headers={"X-Twilio-Signature": _sign(form)})


def test_text_message_never_calls_gemini(app_client, monkeypatch):
    mock_process = AsyncMock(return_value=ProcessResult(reply="ok", outcome="success"))
    mock_send = AsyncMock()
    mock_fetch_media = AsyncMock()
    mock_understand = AsyncMock()
    monkeypatch.setattr(twilio_webhook, "process_user_message", mock_process)
    monkeypatch.setattr(twilio_webhook, "send_text_message", mock_send)
    monkeypatch.setattr(twilio_webhook.media_fetcher, "fetch_media", mock_fetch_media)
    monkeypatch.setattr(twilio_webhook.media_understanding_service, "understand_media", mock_understand)

    form = {"From": "whatsapp:+919876543210", "Body": "show my overdue payments", "MessageSid": "SM_text_1"}
    response = _post(app_client, form)

    assert response.status_code == 200
    mock_fetch_media.assert_not_called()
    mock_understand.assert_not_called()
    mock_process.assert_called_once_with("+919876543210", "show my overdue payments", channel="twilio")


def test_audio_message_goes_through_gemini_then_openai_pipeline(app_client, monkeypatch):
    mock_process = AsyncMock(return_value=ProcessResult(reply="Reminder sent.", outcome="success", tool_name="send_payment_reminder"))
    mock_send = AsyncMock()
    mock_fetch_media = AsyncMock(return_value=b"raw-ogg-bytes")
    mock_understand = AsyncMock(return_value="send Rahul a reminder for his 2500 rupee payment")
    monkeypatch.setattr(twilio_webhook, "process_user_message", mock_process)
    monkeypatch.setattr(twilio_webhook, "send_text_message", mock_send)
    monkeypatch.setattr(twilio_webhook.media_fetcher, "fetch_media", mock_fetch_media)
    monkeypatch.setattr(twilio_webhook.media_understanding_service, "understand_media", mock_understand)

    form = {
        "From": "whatsapp:+919876543210",
        "Body": "",
        "MessageSid": "SM_audio_1",
        "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/voice-note.ogg",
        "MediaContentType0": "audio/ogg",
    }
    response = _post(app_client, form)

    assert response.status_code == 200
    # 1. Gemini received the fetched media bytes + content type.
    mock_fetch_media.assert_called_once_with("https://api.twilio.com/media/voice-note.ogg")
    mock_understand.assert_called_once_with(b"raw-ogg-bytes", "audio/ogg")
    # 2/3. Gemini's transcript reached the same OpenAI-driven pipeline as typed text.
    mock_process.assert_called_once_with(
        "+919876543210", "send Rahul a reminder for his 2500 rupee payment", channel="twilio"
    )
    # 4/5. The pipeline's tool-selection result made it back out to the user via Twilio.
    mock_send.assert_called_once_with("+919876543210", "Reminder sent.")


def test_video_message_goes_through_gemini_then_openai_pipeline(app_client, monkeypatch):
    mock_process = AsyncMock(return_value=ProcessResult(reply="Payment link created.", outcome="success", tool_name="create_payment_link"))
    mock_send = AsyncMock()
    mock_fetch_media = AsyncMock(return_value=b"raw-mp4-bytes")
    mock_understand = AsyncMock(return_value="create a payment link for 5000 rupees, showing an invoice on screen")
    monkeypatch.setattr(twilio_webhook, "process_user_message", mock_process)
    monkeypatch.setattr(twilio_webhook, "send_text_message", mock_send)
    monkeypatch.setattr(twilio_webhook.media_fetcher, "fetch_media", mock_fetch_media)
    monkeypatch.setattr(twilio_webhook.media_understanding_service, "understand_media", mock_understand)

    form = {
        "From": "whatsapp:+919876543210",
        "Body": "",
        "MessageSid": "SM_video_1",
        "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/clip.mp4",
        "MediaContentType0": "video/mp4",
    }
    response = _post(app_client, form)

    assert response.status_code == 200
    mock_fetch_media.assert_called_once_with("https://api.twilio.com/media/clip.mp4")
    mock_understand.assert_called_once_with(b"raw-mp4-bytes", "video/mp4")
    mock_process.assert_called_once_with(
        "+919876543210", "create a payment link for 5000 rupees, showing an invoice on screen", channel="twilio"
    )
    mock_send.assert_called_once_with("+919876543210", "Payment link created.")


def test_media_fetch_failure_sends_graceful_reply_and_skips_pipeline(app_client, monkeypatch):
    mock_process = AsyncMock()
    mock_send = AsyncMock()
    mock_fetch_media = AsyncMock(return_value=None)  # Twilio media download failed
    mock_understand = AsyncMock()
    monkeypatch.setattr(twilio_webhook, "process_user_message", mock_process)
    monkeypatch.setattr(twilio_webhook, "send_text_message", mock_send)
    monkeypatch.setattr(twilio_webhook.media_fetcher, "fetch_media", mock_fetch_media)
    monkeypatch.setattr(twilio_webhook.media_understanding_service, "understand_media", mock_understand)

    form = {
        "From": "whatsapp:+919876543210",
        "Body": "",
        "MessageSid": "SM_fetch_fail",
        "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/unreachable.ogg",
        "MediaContentType0": "audio/ogg",
    }
    response = _post(app_client, form)

    assert response.status_code == 200
    mock_understand.assert_not_called()
    mock_process.assert_not_called()
    mock_send.assert_called_once_with("+919876543210", twilio_webhook.MEDIA_FAILURE_REPLY)


def test_gemini_understanding_failure_sends_graceful_reply_and_skips_pipeline(app_client, monkeypatch):
    mock_process = AsyncMock()
    mock_send = AsyncMock()
    mock_fetch_media = AsyncMock(return_value=b"raw-bytes")
    mock_understand = AsyncMock(return_value=None)  # Gemini unavailable / empty output
    monkeypatch.setattr(twilio_webhook, "process_user_message", mock_process)
    monkeypatch.setattr(twilio_webhook, "send_text_message", mock_send)
    monkeypatch.setattr(twilio_webhook.media_fetcher, "fetch_media", mock_fetch_media)
    monkeypatch.setattr(twilio_webhook.media_understanding_service, "understand_media", mock_understand)

    form = {
        "From": "whatsapp:+919876543210",
        "Body": "",
        "MessageSid": "SM_gemini_fail",
        "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/voice.ogg",
        "MediaContentType0": "audio/ogg",
    }
    response = _post(app_client, form)

    assert response.status_code == 200
    mock_process.assert_not_called()
    mock_send.assert_called_once_with("+919876543210", twilio_webhook.MEDIA_FAILURE_REPLY)


def test_unsupported_media_type_sends_graceful_reply_without_calling_gemini(app_client, monkeypatch):
    mock_process = AsyncMock()
    mock_send = AsyncMock()
    mock_fetch_media = AsyncMock()
    mock_understand = AsyncMock()
    monkeypatch.setattr(twilio_webhook, "process_user_message", mock_process)
    monkeypatch.setattr(twilio_webhook, "send_text_message", mock_send)
    monkeypatch.setattr(twilio_webhook.media_fetcher, "fetch_media", mock_fetch_media)
    monkeypatch.setattr(twilio_webhook.media_understanding_service, "understand_media", mock_understand)

    form = {
        "From": "whatsapp:+919876543210",
        "Body": "",
        "MessageSid": "SM_image_1",
        "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/photo.png",
        "MediaContentType0": "image/png",
    }
    response = _post(app_client, form)

    assert response.status_code == 200
    mock_fetch_media.assert_not_called()
    mock_understand.assert_not_called()
    mock_process.assert_not_called()
    mock_send.assert_called_once_with("+919876543210", twilio_webhook.MEDIA_FAILURE_REPLY)
