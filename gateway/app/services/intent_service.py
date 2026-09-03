"""
Intent understanding via native OpenAI GPT tool-calling — genuine semantic
understanding of the user's message, never hardcoded keyword/regex routing.
The tool schema is generated once from the MCP server's own list_tools()
output (already JSON-Schema-shaped from server.py's type hints/docstrings)
— one source of truth, no hand-duplicated prose description to drift out
of sync.

OpenAI GPT is the ONLY reasoning/intent-understanding/tool-selection engine
in this codebase — deliberately, not just by default. Gemini is used
elsewhere (app/services/media_understanding_service.py) solely to turn
audio/video into plain text; by the time a message reaches select_tool()
below, whether it originated as typed text or as a Gemini transcript is
already indistinguishable, and it is reasoned about by OpenAI alone.

Hard boundary preserved here: this module ONLY selects a tool and extracts
arguments. It never resolves ambiguity — a tool result coming back
{"ambiguous": true, ...} is handled deterministically by message_processor,
never handed back to the LLM to "pick the best match".
"""

import copy
import dataclasses
import json
import logging
from typing import Any, Optional

from app.config import settings
from app.mcp import mini_razorpay_mcp_client as mcp_client

logger = logging.getLogger(__name__)

# Never shown to / fillable by the LLM — injected server-side by the
# orchestrator (access_token from merchant_auth_service, idempotency_key
# generated fresh per new logical action, existing_payment_id set only by
# message_processor's automatic post-reminder payment-link flow — never
# something the LLM decides to attach or invents a paymentId for).
_HIDDEN_ARGUMENTS = {"access_token", "idempotency_key", "existing_payment_id"}

SYSTEM_PROMPT = """You are the intent-understanding layer for Razorpay Sugam, \
a WhatsApp assistant that lets a merchant manage their Mini-Razorpay payments \
by chatting in natural language (English or Hindi/Hinglish).

Call exactly one tool if the merchant's message clearly asks for a payments/\
customers/reminders/settlements action or question. If the message is \
ambiguous about WHICH tool to use, or is missing information a tool \
genuinely needs to proceed (e.g. an amount for a payment link), do not call \
a tool — instead reply with a short, natural clarifying question asking for \
exactly what's missing. If the message is unrelated to Mini-Razorpay \
entirely (small talk, unrelated topics), reply briefly and naturally without \
calling a tool.

Never invent a paymentId, customerId, amount, or any other data value that \
was not given to you by the user or by a previous tool result already shown \
to you in this conversation. If a tool result says a request was ambiguous \
with several candidates, you will not be asked to pick one — that is handled \
outside of you; do not attempt to resolve it yourself."""


@dataclasses.dataclass
class ToolSelection:
    tool_name: Optional[str]
    arguments: dict
    reply_text: Optional[str]  # set when no tool was called (no_tool outcome)


def _strip_hidden(schema: dict) -> dict:
    schema = copy.deepcopy(schema)
    props = schema.get("properties", {})
    for key in _HIDDEN_ARGUMENTS:
        props.pop(key, None)
    return schema


async def _build_tool_schemas() -> list[dict]:
    mcp_tools = await mcp_client.list_tools()
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": _strip_hidden(t.inputSchema),
            },
        }
        for t in mcp_tools
    ]


_cached_schemas: list[dict] | None = None


async def _get_tool_schemas() -> list[dict]:
    global _cached_schemas
    if _cached_schemas is None:
        _cached_schemas = await _build_tool_schemas()
    return _cached_schemas


async def select_tool(message: str, context: Optional[str] = None) -> ToolSelection:
    tools = await _get_tool_schemas()

    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context:
        messages.append({"role": "system", "content": context})
    messages.append({"role": "user", "content": message})

    return await _select_tool_openai(messages, tools)


async def _select_tool_openai(messages: list[dict], tools: list[dict]) -> ToolSelection:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    kwargs: dict[str, Any] = {"model": settings.openai_model, "messages": messages, "tools": tools, "tool_choice": "auto"}

    response = await client.chat.completions.create(**kwargs)
    choice = response.choices[0].message

    if choice.tool_calls:
        call = choice.tool_calls[0]
        try:
            arguments = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            logger.warning("OpenAI returned non-JSON tool arguments: %r", call.function.arguments)
            arguments = {}
        return ToolSelection(tool_name=call.function.name, arguments=arguments, reply_text=None)

    return ToolSelection(tool_name=None, arguments={}, reply_text=choice.content or "")
