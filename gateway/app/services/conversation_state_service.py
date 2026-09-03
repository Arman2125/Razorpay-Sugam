"""
The generic pending-clarification store — one active row per
(whatsapp_number, capability), mirroring Sugam AI OS's own
GenericActionStateRow design. This is the deterministic half of the "never
guess on ambiguity" contract: message_processor consults this BEFORE any LLM
call, and resolves a reply only against the exact candidates stored here.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ConversationState


async def get_active_state(session: AsyncSession, whatsapp_number: str) -> Optional[ConversationState]:
    now = datetime.now(timezone.utc)
    state = await session.scalar(
        select(ConversationState).where(
            ConversationState.whatsapp_number == whatsapp_number,
            ConversationState.status == "active",
        )
    )
    if state is None:
        return None
    if state.expires_at and state.expires_at < now:
        state.status = "expired"
        await session.commit()
        return None
    return state


async def create_state(
    session: AsyncSession,
    whatsapp_number: str,
    capability: str,
    tool_name: str,
    original_arguments: dict,
    candidates: list[dict],
) -> ConversationState:
    now = datetime.now(timezone.utc)
    state = ConversationState(
        whatsapp_number=whatsapp_number,
        capability=capability,
        status="active",
        payload={
            "tool_name": tool_name,
            "original_arguments": original_arguments,
            "candidates": candidates,
            "attempts": 0,
        },
        created_at=now,
        expires_at=now + timedelta(seconds=settings.conversation_state_ttl_seconds),
    )
    session.add(state)
    await session.commit()
    await session.refresh(state)
    return state


async def resolve_state(session: AsyncSession, state: ConversationState) -> None:
    state.status = "resolved"
    state.resolved_at = datetime.now(timezone.utc)
    await session.commit()


async def expire_state(session: AsyncSession, state: ConversationState) -> None:
    state.status = "expired"
    await session.commit()


async def increment_attempts(session: AsyncSession, state: ConversationState) -> int:
    payload = dict(state.payload)
    payload["attempts"] = payload.get("attempts", 0) + 1
    state.payload = payload
    await session.commit()
    return payload["attempts"]


_NUMBER_WORDS = {"first": 1, "one": 1, "second": 2, "two": 2, "third": 3, "three": 3, "fourth": 4, "four": 4}


def parse_candidate_index(reply: str, candidate_count: int) -> Optional[int]:
    """Returns a 0-based index into the candidates list, or None if the
    reply couldn't be deterministically matched to exactly one candidate.
    Never guesses — a reply like 'the payment' with no number is None, not
    an arbitrary pick."""
    text = (reply or "").strip().lower()

    if text.isdigit():
        n = int(text)
        return n - 1 if 1 <= n <= candidate_count else None

    for word, n in _NUMBER_WORDS.items():
        if word in text and 1 <= n <= candidate_count:
            return n - 1

    import re

    match = re.search(r"\b(\d+)\b", text)
    if match:
        n = int(match.group(1))
        return n - 1 if 1 <= n <= candidate_count else None

    return None
