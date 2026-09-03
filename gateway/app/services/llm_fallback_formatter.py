"""
Narrow, constrained LLM-generated fallback — used ONLY when a tool result's
error shape is genuinely unrecognized by response_formatting.format_known_error's
lookup. Mirrors Sugam AI OS's _generate_dynamic_fallback_reply(): never
invents a specific cause it wasn't given, returns None on any failure so the
caller falls back to a fixed generic message instead of a broken/empty reply.
Off by default (settings.llm_fallback_enabled). Uses OpenAI GPT, the same
reasoning engine as intent_service.py — Gemini is never used here or
anywhere for text; its only role in this codebase is audio/video
understanding (see app/services/media_understanding_service.py).
"""

import logging

from app.config import settings

logger = logging.getLogger(__name__)

_FALLBACK_PROMPT = """Write ONE short, natural, plain-text WhatsApp message \
telling the merchant that their request could not be completed right now. \
Do not invent a specific reason you were not given — keep it general and \
suggest trying again shortly. No more than two sentences."""

GENERIC_FAILURE_MESSAGE = "Sorry, something went wrong completing that. Please try again in a moment."


async def generate_fallback_reply() -> str:
    if not settings.llm_fallback_enabled:
        return GENERIC_FAILURE_MESSAGE

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "user", "content": _FALLBACK_PROMPT}],
            max_tokens=60,
        )
        text = response.choices[0].message.content

        return text.strip() if text else GENERIC_FAILURE_MESSAGE
    except Exception:
        logger.exception("LLM fallback reply generation failed")
        return GENERIC_FAILURE_MESSAGE
