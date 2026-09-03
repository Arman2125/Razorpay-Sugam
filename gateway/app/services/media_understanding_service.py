"""Gemini-based audio/video understanding — the ONLY place Gemini is called
anywhere in this codebase. Turns a WhatsApp voice note or video into a
plain-text transcript/description that the caller then feeds into the
existing message_processor.process_user_message() pipeline exactly as if
the user had typed it — so intent_service.py's OpenAI GPT call remains the
sole reasoning/tool-selection step regardless of whether the text
originated as typed input or a Gemini transcript.

Gemini is never used for text reasoning or tool selection here — see
config.py's comments on openai_* vs gemini_* settings for the enforced
split. This module is channel-agnostic (raw bytes + a MIME type in, plain
text or None out) so any channel's webhook can reuse it unchanged — a
webhook only needs its own authenticated media download to produce the
raw bytes this module expects."""

import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

_AUDIO_PROMPT = (
    "Transcribe this voice message from a merchant to a WhatsApp payments "
    "assistant into plain text, exactly as if the merchant had typed it "
    "themselves. Output only the transcription, nothing else."
)

_VIDEO_PROMPT = (
    "This is a video message from a merchant to a WhatsApp payments "
    "assistant. Write, in plain text exactly as if the merchant had typed "
    "it themselves, what they are asking for — based on both what is said "
    "out loud and anything relevant shown on screen (e.g. numbers, names, "
    "a document or invoice). Output only that plain-text message, nothing "
    "else."
)


def _is_audio(content_type: str) -> bool:
    return content_type.startswith("audio/")


def _is_video(content_type: str) -> bool:
    return content_type.startswith("video/")


def is_supported_media(content_type: str) -> bool:
    return _is_audio(content_type) or _is_video(content_type)


async def understand_media(media_bytes: bytes, content_type: str) -> Optional[str]:
    """Returns a plain-text transcript/description, or None if it couldn't
    produce one — misconfigured, unsupported type, API failure, or empty
    output. Callers must treat None as "processing failed" and reply with a
    safe, generic message rather than passing anything further downstream."""
    if not settings.gemini_api_key or not settings.gemini_model:
        logger.warning("Gemini is not configured — cannot process audio/video media")
        return None

    if _is_audio(content_type):
        prompt = _AUDIO_PROMPT
    elif _is_video(content_type):
        prompt = _VIDEO_PROMPT
    else:
        logger.warning("Unsupported media type for Gemini understanding: %s", content_type)
        return None

    try:
        import google.generativeai as genai

        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(model_name=settings.gemini_model)
        response = await model.generate_content_async(
            [{"mime_type": content_type, "data": media_bytes}, prompt]
        )
        text = (response.text or "").strip()
    except Exception:
        logger.exception("Gemini media understanding failed")
        return None

    return text or None
