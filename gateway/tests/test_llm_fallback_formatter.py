"""llm_fallback_formatter must always use OpenAI GPT — never silently
substitute Gemini, even when OpenAI itself fails and the reply degrades to
a hardcoded generic message."""

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import llm_fallback_formatter as fallback


class _ExplodingModule(types.ModuleType):
    def __getattr__(self, name):
        raise AssertionError("fallback formatter must never touch google.generativeai")


@pytest.mark.asyncio
async def test_disabled_returns_generic_message_without_calling_openai(monkeypatch):
    monkeypatch.setattr(fallback.settings, "llm_fallback_enabled_raw", "false")

    fake_openai = types.ModuleType("openai")
    fake_openai.AsyncOpenAI = MagicMock(side_effect=AssertionError("must not be called"))

    with patch.dict(sys.modules, {"openai": fake_openai}):
        result = await fallback.generate_fallback_reply()

    assert result == fallback.GENERIC_FAILURE_MESSAGE


@pytest.mark.asyncio
async def test_enabled_calls_openai_and_returns_its_text(monkeypatch):
    monkeypatch.setattr(fallback.settings, "llm_fallback_enabled_raw", "true")
    monkeypatch.setattr(fallback.settings, "openai_api_key", "test_key")
    monkeypatch.setattr(fallback.settings, "openai_model", "gpt-4o-mini")

    message = MagicMock()
    message.content = "Sorry, something went wrong — please try again shortly."
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]

    fake_openai = types.ModuleType("openai")
    mock_client_instance = MagicMock()
    mock_client_instance.chat.completions.create = AsyncMock(return_value=response)
    fake_openai.AsyncOpenAI = MagicMock(return_value=mock_client_instance)

    with patch.dict(sys.modules, {"openai": fake_openai, "google.generativeai": _ExplodingModule("google.generativeai")}):
        result = await fallback.generate_fallback_reply()

    assert result == "Sorry, something went wrong — please try again shortly."
    mock_client_instance.chat.completions.create.assert_called_once()


@pytest.mark.asyncio
async def test_openai_failure_degrades_to_generic_message_never_touching_gemini(monkeypatch):
    monkeypatch.setattr(fallback.settings, "llm_fallback_enabled_raw", "true")
    monkeypatch.setattr(fallback.settings, "openai_api_key", "test_key")
    monkeypatch.setattr(fallback.settings, "openai_model", "gpt-4o-mini")

    fake_openai = types.ModuleType("openai")
    mock_client_instance = MagicMock()
    mock_client_instance.chat.completions.create = AsyncMock(side_effect=RuntimeError("OpenAI is unavailable"))
    fake_openai.AsyncOpenAI = MagicMock(return_value=mock_client_instance)

    with patch.dict(sys.modules, {"openai": fake_openai, "google.generativeai": _ExplodingModule("google.generativeai")}):
        result = await fallback.generate_fallback_reply()

    assert result == fallback.GENERIC_FAILURE_MESSAGE
