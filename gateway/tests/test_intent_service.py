"""intent_service.select_tool() must always reason/select tools via OpenAI
GPT — Gemini must never be touched, even implicitly. Mocks the MCP client
(list_tools) and the OpenAI SDK; no live credentials, no network."""

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import intent_service


class _FakeTool:
    def __init__(self, name, description, input_schema):
        self.name = name
        self.description = description
        self.inputSchema = input_schema


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    intent_service._cached_schemas = None
    yield
    intent_service._cached_schemas = None


def _fake_list_tools():
    return AsyncMock(
        return_value=[
            _FakeTool(
                "get_payments_summary",
                "Get a summary of payments",
                {"type": "object", "properties": {"access_token": {"type": "string"}}},
            )
        ]
    )


def _fake_openai_module(tool_call_name=None, tool_call_args="{}", content=None):
    fake_openai = types.ModuleType("openai")

    message = MagicMock()
    message.content = content
    if tool_call_name:
        call = MagicMock()
        call.function.name = tool_call_name
        call.function.arguments = tool_call_args
        message.tool_calls = [call]
    else:
        message.tool_calls = None

    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]

    mock_client_instance = MagicMock()
    mock_client_instance.chat.completions.create = AsyncMock(return_value=response)
    fake_openai.AsyncOpenAI = MagicMock(return_value=mock_client_instance)
    return fake_openai, mock_client_instance


class _ExplodingModule(types.ModuleType):
    """Stands in for google.generativeai — any attribute access fails the
    test, proving select_tool() never imports or touches it."""

    def __getattr__(self, name):
        raise AssertionError("select_tool() must never touch google.generativeai")


@pytest.mark.asyncio
async def test_select_tool_calls_openai_and_never_gemini(monkeypatch):
    monkeypatch.setattr(intent_service.mcp_client, "list_tools", _fake_list_tools())
    fake_openai, mock_client = _fake_openai_module(tool_call_name="get_payments_summary", tool_call_args="{}")

    with patch.dict(
        sys.modules,
        {"openai": fake_openai, "google.generativeai": _ExplodingModule("google.generativeai")},
    ):
        result = await intent_service.select_tool("what are my total payments?")

    assert result.tool_name == "get_payments_summary"
    assert result.arguments == {}
    mock_client.chat.completions.create.assert_called_once()


@pytest.mark.asyncio
async def test_select_tool_with_no_tool_call_returns_reply_text(monkeypatch):
    monkeypatch.setattr(intent_service.mcp_client, "list_tools", _fake_list_tools())
    fake_openai, _ = _fake_openai_module(tool_call_name=None, content="Sure, what's the amount?")

    with patch.dict(sys.modules, {"openai": fake_openai}):
        result = await intent_service.select_tool("send a reminder")

    assert result.tool_name is None
    assert result.reply_text == "Sure, what's the amount?"


@pytest.mark.asyncio
async def test_select_tool_propagates_openai_failure_without_falling_back_to_gemini(monkeypatch):
    monkeypatch.setattr(intent_service.mcp_client, "list_tools", _fake_list_tools())

    fake_openai = types.ModuleType("openai")
    mock_client_instance = MagicMock()
    mock_client_instance.chat.completions.create = AsyncMock(side_effect=RuntimeError("OpenAI is unavailable"))
    fake_openai.AsyncOpenAI = MagicMock(return_value=mock_client_instance)

    with patch.dict(
        sys.modules,
        {"openai": fake_openai, "google.generativeai": _ExplodingModule("google.generativeai")},
    ):
        with pytest.raises(RuntimeError):
            await intent_service.select_tool("what are my pending payments?")


def test_select_tool_gemini_function_no_longer_exists():
    # Enforces the architecture at the code level: there must be no code
    # path in this module that can hand reasoning/tool-selection to Gemini.
    assert not hasattr(intent_service, "_select_tool_gemini")
