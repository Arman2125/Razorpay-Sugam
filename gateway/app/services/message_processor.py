"""
The orchestrator — enforces the "never guess" contract end-to-end. This is
the single most important file in the project: every ambiguity resolution
happens here, deterministically, never by asking the LLM to pick a
candidate.

Flow (channel-independent — WhatsApp and the /test/message endpoint both
call process_user_message() with the same signature):

  1. Check for an active conversation_states row FIRST, always, before any
     LLM call — a pending clarification is resolved deterministically
     against its own stored candidates only.
  2. No pending state -> resolve merchant identity from the phone number.
     Unresolved -> decline, no LLM call.
  3. intent_service.select_tool() -> genuine LLM semantic understanding.
  4. Get the merchant's own JWT, call the selected MCP tool.
  5. Inspect the structured result: ambiguous / known error / success /
     unrecognized -> format the appropriate reply.
  6. Log to gateway_activity_log.
"""

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime

from app.db import AsyncSessionLocal
from app.models import GatewayActivityLog
from app.services import (
    conversation_state_service,
    identity_service,
    intent_service,
    llm_fallback_formatter,
    merchant_auth_service,
    payment_recovery_notifier,
    response_formatting,
)
from app.mcp import mini_razorpay_mcp_client as mcp_client

logger = logging.getLogger(__name__)

# Tools whose write is worth an idempotency key — generated fresh server-side
# per new logical action, never supplied or invented by the LLM. Re-used
# unchanged across clarification retries of the SAME action (see step 1),
# so a resolved ambiguous call is still deduped correctly if retried.
_IDEMPOTENT_TOOLS = {"send_payment_reminder", "create_payment_link"}


@dataclass
class ProcessResult:
    reply: str
    outcome: str
    tool_name: str | None = None
    error_code: str | None = None


async def process_user_message(whatsapp_number: str, message: str, channel: str = "test") -> ProcessResult:
    start = time.time()
    async with AsyncSessionLocal() as session:
        try:
            result = await _process(session, whatsapp_number, message, channel)
        except Exception:
            logger.exception("Unhandled error processing message from %s", whatsapp_number)
            result = ProcessResult(
                reply=await llm_fallback_formatter.generate_fallback_reply(),
                outcome="error",
                error_code="UNHANDLED_EXCEPTION",
            )

        latency_ms = int((time.time() - start) * 1000)
        session.add(
            GatewayActivityLog(
                whatsapp_number=whatsapp_number,
                channel=channel,
                incoming_message=message,
                tool_name=result.tool_name,
                outcome=result.outcome,
                error_code=result.error_code,
                latency_ms=latency_ms,
            )
        )
        await session.commit()

    return result


async def _process(session, whatsapp_number: str, message: str, channel: str) -> ProcessResult:
    # ---- Step 1: an active clarification always wins, no LLM involved ----
    state = await conversation_state_service.get_active_state(session, whatsapp_number)
    if state is not None:
        return await _resolve_pending_state(session, whatsapp_number, state, message, channel)

    # ---- Step 2: identity resolution ----
    merchant = await identity_service.resolve_merchant(session, whatsapp_number)
    if merchant is None:
        return ProcessResult(reply=response_formatting.format_declined_unregistered(), outcome="declined")

    # ---- Step 3: genuine LLM intent understanding ----
    selection = await intent_service.select_tool(message)

    if selection.tool_name is None:
        return ProcessResult(reply=selection.reply_text or "Okay.", outcome="no_tool")

    # ---- Step 4: get the merchant's own JWT, execute the tool ----
    try:
        token = await merchant_auth_service.get_jwt(session, merchant)
    except merchant_auth_service.MerchantAuthError as e:
        return ProcessResult(reply=f"I couldn't verify your merchant account right now: {e.message}", outcome="error")

    arguments = dict(selection.arguments)
    if selection.tool_name in _IDEMPOTENT_TOOLS:
        arguments.setdefault("idempotency_key", f"wa:{whatsapp_number}:{uuid.uuid4().hex}")

    tool_result = await mcp_client.call_tool(selection.tool_name, arguments, token)

    return await _handle_tool_result(
        session, whatsapp_number, selection.tool_name, arguments, tool_result, channel=channel, merchant=merchant, token=token
    )


async def _handle_tool_result(
    session,
    whatsapp_number: str,
    tool_name: str,
    arguments: dict,
    tool_result: dict,
    *,
    channel: str = "test",
    merchant=None,
    token: str | None = None,
) -> ProcessResult:
    if tool_result.get("ambiguous"):
        candidates = tool_result.get("candidates", [])
        await conversation_state_service.create_state(
            session, whatsapp_number, capability=tool_name, tool_name=tool_name,
            original_arguments=arguments, candidates=candidates,
        )
        reply = response_formatting.format_clarification(tool_name, candidates)
        return ProcessResult(reply=reply, outcome="ambiguous", tool_name=tool_name, error_code=tool_result.get("code"))

    if tool_result.get("duplicate"):
        reply = response_formatting.format_known_error(tool_result.get("code", "DUPLICATE_REMINDER"), tool_result.get("message", ""))
        return ProcessResult(reply=reply, outcome="duplicate", tool_name=tool_name, error_code=tool_result.get("code"))

    if tool_result.get("error"):
        code = tool_result.get("code", "UNKNOWN_ERROR")
        known_codes = {
            "DUPLICATE_REMINDER", "INVALID_TRANSITION", "INVALID_STATUS", "INVALID_AMOUNT",
            "PAYMENT_NOT_FOUND", "CUSTOMER_NOT_FOUND", "NO_STATE_CHANGE",
        }
        if code in known_codes:
            reply = response_formatting.format_known_error(code, tool_result.get("message", ""))
        else:
            reply = await llm_fallback_formatter.generate_fallback_reply()
        return ProcessResult(reply=reply, outcome="error", tool_name=tool_name, error_code=code)

    if tool_result.get("success"):
        reply = response_formatting.format_tool_success(tool_name, tool_result)

        # A genuinely successful reminder also messages the customer
        # directly with a real payment link, and starts watching for them
        # to pay it (see payment_recovery_notifier.py) so the merchant gets
        # an automatic WhatsApp confirmation later. Best-effort: never lets
        # a failure here downgrade the merchant's own "Reminder sent."
        if tool_name == "send_payment_reminder" and merchant is not None and token is not None:
            reminder = tool_result.get("reminder", {})
            payment_id = reminder.get("paymentId")
            customer_id = reminder.get("customerId")
            if payment_id and customer_id:
                notified = await payment_recovery_notifier.notify_customer_and_watch_for_payment(
                    session,
                    merchant_id=merchant.merchant_id,
                    merchant_whatsapp_number=whatsapp_number,
                    channel=channel,
                    token=token,
                    payment_id=payment_id,
                    customer_id=customer_id,
                )
                if notified:
                    reply = "Reminder sent — I've also messaged the customer directly with a payment link."

        return ProcessResult(reply=reply, outcome="success", tool_name=tool_name)

    reply = await llm_fallback_formatter.generate_fallback_reply()
    return ProcessResult(reply=reply, outcome="error", tool_name=tool_name, error_code="UNRECOGNIZED_RESULT_SHAPE")


async def _resolve_pending_state(session, whatsapp_number: str, state, message: str, channel: str) -> ProcessResult:
    candidates = state.payload["candidates"]
    tool_name = state.payload["tool_name"]

    index = conversation_state_service.parse_candidate_index(message, len(candidates))

    if index is None:
        attempts = await conversation_state_service.increment_attempts(session, state)
        if attempts >= 3:
            await conversation_state_service.expire_state(session, state)
            return ProcessResult(reply=response_formatting.format_reprompt_expired(), outcome="declined")
        return ProcessResult(reply=response_formatting.format_reprompt(), outcome="ambiguous", tool_name=tool_name)

    chosen = candidates[index]
    merged_arguments = dict(state.payload["original_arguments"])

    # Merge the chosen candidate's identifying field so the retried call is
    # unambiguous by construction — never re-ask the LLM to disambiguate.
    if tool_name == "send_payment_reminder":
        merged_arguments["payment_id"] = chosen["paymentId"]
        merged_arguments.pop("customer_name", None)
        merged_arguments.pop("customer_id", None)
        merged_arguments.pop("amount", None)
    elif tool_name == "create_payment_link":
        merged_arguments["customer_id"] = chosen["customerId"]
        merged_arguments.pop("customer_name", None)

    await conversation_state_service.resolve_state(session, state)

    merchant = await identity_service.resolve_merchant(session, whatsapp_number)
    if merchant is None:
        return ProcessResult(reply=response_formatting.format_declined_unregistered(), outcome="declined")

    try:
        token = await merchant_auth_service.get_jwt(session, merchant)
    except merchant_auth_service.MerchantAuthError as e:
        return ProcessResult(reply=f"I couldn't verify your merchant account right now: {e.message}", outcome="error")

    tool_result = await mcp_client.call_tool(tool_name, merged_arguments, token)
    return await _handle_tool_result(
        session, whatsapp_number, tool_name, merged_arguments, tool_result, channel=channel, merchant=merchant, token=token
    )
