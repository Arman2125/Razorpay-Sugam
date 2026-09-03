"""Gemini media understanding — mocked at the google.generativeai boundary,
no live Gemini credentials required. Also verifies OpenAI is never touched
by this module (its only job is producing text for OpenAI to reason about
later, in a separate step)."""

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import media_understanding_service as media_service


def _fake_genai_module(generated_text: str | None):
    fake_genai = types.ModuleType("google.generativeai")
    fake_genai.configure = MagicMock()

    mock_response = MagicMock()
    mock_response.text = generated_text

    mock_model_instance = MagicMock()
    mock_model_instance.generate_content_async = AsyncMock(return_value=mock_response)
    fake_genai.GenerativeModel = MagicMock(return_value=mock_model_instance)
    return fake_genai, mock_model_instance


@pytest.mark.asyncio
async def test_not_configured_returns_none_without_calling_gemini(monkeypatch):
    monkeypatch.setattr(media_service.settings, "gemini_api_key", "")
    monkeypatch.setattr(media_service.settings, "gemini_model", "")

    result = await media_service.understand_media(b"fake-audio-bytes", "audio/ogg")

    assert result is None


@pytest.mark.asyncio
async def test_unsupported_media_type_returns_none_without_calling_gemini(monkeypatch):
    monkeypatch.setattr(media_service.settings, "gemini_api_key", "test_key")
    monkeypatch.setattr(media_service.settings, "gemini_model", "gemini-1.5-flash")

    fake_genai, mock_model = _fake_genai_module("should never be reached")
    with patch.dict(sys.modules, {"google.generativeai": fake_genai}):
        result = await media_service.understand_media(b"fake-image-bytes", "image/png")

    assert result is None
    mock_model.generate_content_async.assert_not_called()


@pytest.mark.asyncio
async def test_audio_is_sent_to_gemini_and_transcript_is_returned(monkeypatch):
    monkeypatch.setattr(media_service.settings, "gemini_api_key", "test_key")
    monkeypatch.setattr(media_service.settings, "gemini_model", "gemini-1.5-flash")

    fake_genai, mock_model = _fake_genai_module("send Rahul a reminder for his 2500 rupee payment")
    with patch.dict(sys.modules, {"google.generativeai": fake_genai}):
        result = await media_service.understand_media(b"fake-audio-bytes", "audio/ogg")

    assert result == "send Rahul a reminder for his 2500 rupee payment"
    # Gemini received the raw media bytes + a MIME type, not text.
    call_args = mock_model.generate_content_async.call_args[0][0]
    assert {"mime_type": "audio/ogg", "data": b"fake-audio-bytes"} in call_args


@pytest.mark.asyncio
async def test_video_is_sent_to_gemini_and_description_is_returned(monkeypatch):
    monkeypatch.setattr(media_service.settings, "gemini_api_key", "test_key")
    monkeypatch.setattr(media_service.settings, "gemini_model", "gemini-1.5-flash")

    fake_genai, mock_model = _fake_genai_module("create a payment link for 5000 rupees for this customer")
    with patch.dict(sys.modules, {"google.generativeai": fake_genai}):
        result = await media_service.understand_media(b"fake-video-bytes", "video/mp4")

    assert result == "create a payment link for 5000 rupees for this customer"
    call_args = mock_model.generate_content_async.call_args[0][0]
    assert {"mime_type": "video/mp4", "data": b"fake-video-bytes"} in call_args


@pytest.mark.asyncio
async def test_empty_gemini_output_returns_none(monkeypatch):
    monkeypatch.setattr(media_service.settings, "gemini_api_key", "test_key")
    monkeypatch.setattr(media_service.settings, "gemini_model", "gemini-1.5-flash")

    fake_genai, _ = _fake_genai_module("")
    with patch.dict(sys.modules, {"google.generativeai": fake_genai}):
        result = await media_service.understand_media(b"fake-audio-bytes", "audio/ogg")

    assert result is None


@pytest.mark.asyncio
async def test_gemini_exception_is_caught_and_returns_none(monkeypatch):
    monkeypatch.setattr(media_service.settings, "gemini_api_key", "test_key")
    monkeypatch.setattr(media_service.settings, "gemini_model", "gemini-1.5-flash")

    fake_genai = types.ModuleType("google.generativeai")
    fake_genai.configure = MagicMock()
    mock_model_instance = MagicMock()
    mock_model_instance.generate_content_async = AsyncMock(side_effect=RuntimeError("Gemini is unavailable"))
    fake_genai.GenerativeModel = MagicMock(return_value=mock_model_instance)

    with patch.dict(sys.modules, {"google.generativeai": fake_genai}):
        result = await media_service.understand_media(b"fake-audio-bytes", "audio/ogg")

    assert result is None


def test_is_supported_media():
    assert media_service.is_supported_media("audio/ogg") is True
    assert media_service.is_supported_media("video/mp4") is True
    assert media_service.is_supported_media("image/png") is False
    assert media_service.is_supported_media("application/pdf") is False
