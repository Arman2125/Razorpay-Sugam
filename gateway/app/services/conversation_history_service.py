"""
The generic, capability-agnostic multi-turn conversation memory. Persists the
actual OpenAI-format transcript (user / assistant / tool messages) per
WhatsApp number and reconstructs it as real chat history for the next
message, so intent_service's LLM call reasons over what it already asked/was
told instead of seeing every message as an isolated, context-free request.

Deliberately dumb: this module has no idea what a "payment link" or a
"reminder" is, and never inspects message content to decide anything. It only
records what happened (a user message, an assistant reply, an assistant tool
call + its result) and replays it back in OpenAI's own message shape. All
interpretation — completion, correction, task-switching, confirmation — is
left entirely to the LLM having real history to reason over; nothing here
special-cases any tool name or keyword. Bounded on both time
(CONVERSATION_HISTORY_TTL_SECONDS) and turn count
(CONVERSATION_HISTORY_MAX_TURNS) so context never grows without limit; a tool
result is truncated before being stored (CONVERSATION_HISTORY_MAX_TOOL_RESULT_CHARS)
so one large list response doesn't balloon every later turn's token cost.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ConversationMessage

logger = logging.getLogger(__name__)


def _cutoff(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now - timedelta(seconds=settings.conversation_history_ttl_seconds)


def _truncate(text: str) -> str:
    limit = settings.conversation_history_max_tool_result_chars
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"


def _serialize_tool_result(result: Any) -> str:
    try:
        text = json.dumps(result)
    except (TypeError, ValueError):
        text = str(result)
    return _truncate(text)


def _to_openai_message(row: ConversationMessage) -> dict:
    if row.role == "user":
        return {"role": "user", "content": row.content or ""}

    if row.role == "tool":
        return {"role": "tool", "tool_call_id": row.tool_call_id, "content": row.content or ""}

    # role == "assistant"
    if row.tool_name:
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": row.tool_call_id,
                    "type": "function",
                    "function": {
                        "name": row.tool_name,
                        "arguments": json.dumps(row.tool_arguments or {}),
                    },
                }
            ],
        }
    return {"role": "assistant", "content": row.content or ""}


async def get_recent_messages(session: AsyncSession, whatsapp_number: str) -> list[dict]:
    """Returns the recent transcript for this number, oldest-first, already
    reconstructed into OpenAI's message format — ready to splice directly
    into a chat completion's `messages` list ahead of the new user message."""
    rows = (
        await session.scalars(
            select(ConversationMessage)
            .where(
                ConversationMessage.whatsapp_number == whatsapp_number,
                ConversationMessage.created_at >= _cutoff(),
            )
            .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
            .limit(settings.conversation_history_max_turns)
        )
    ).all()

    rows = list(reversed(rows))  # oldest-first for correct chat ordering
    return [_to_openai_message(row) for row in rows]


async def _prune_expired(session: AsyncSession, whatsapp_number: str) -> None:
    await session.execute(
        delete(ConversationMessage).where(
            ConversationMessage.whatsapp_number == whatsapp_number,
            ConversationMessage.created_at < _cutoff(),
        )
    )


async def record_user_message(session: AsyncSession, whatsapp_number: str, text: str) -> None:
    await _prune_expired(session, whatsapp_number)
    session.add(ConversationMessage(whatsapp_number=whatsapp_number, role="user", content=text))
    await session.commit()


async def record_assistant_reply(session: AsyncSession, whatsapp_number: str, text: str) -> None:
    """The plain "no tool call" case — a clarifying question, a natural
    conversational reply, or the final human-readable reply after a tool
    outcome. This is the exact branch that previously persisted nothing at
    all, which is why a clarification round-trip had no memory of itself."""
    session.add(ConversationMessage(whatsapp_number=whatsapp_number, role="assistant", content=text))
    await session.commit()


async def record_tool_exchange(
    session: AsyncSession,
    whatsapp_number: str,
    tool_call_id: str,
    tool_name: str,
    arguments: dict,
    result: Any,
) -> None:
    """Records the paired assistant-tool-call + tool-result messages, in
    OpenAI's own multi-turn tool-calling shape, so a later turn can see both
    what was called and what it returned."""
    session.add(
        ConversationMessage(
            whatsapp_number=whatsapp_number,
            role="assistant",
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_arguments=arguments,
        )
    )
    session.add(
        ConversationMessage(
            whatsapp_number=whatsapp_number,
            role="tool",
            tool_call_id=tool_call_id,
            content=_serialize_tool_result(result),
        )
    )
    await session.commit()
