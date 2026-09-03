"""Test endpoint — exercises process_user_message() over the 'test' channel,
mirrors Sugam AI OS's own /test/message route, so the whole pipeline is
testable via plain HTTP without any WhatsApp setup."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.message_processor import process_user_message

router = APIRouter()


class TestMessageRequest(BaseModel):
    user_id: str  # a phone number standing in for the WhatsApp sender
    message: str


@router.post("/test/message")
async def handle_test_message(payload: TestMessageRequest):
    result = await process_user_message(payload.user_id, payload.message, channel="test")
    return {
        "reply": result.reply,
        "outcome": result.outcome,
        "tool_name": result.tool_name,
        "error_code": result.error_code,
    }
