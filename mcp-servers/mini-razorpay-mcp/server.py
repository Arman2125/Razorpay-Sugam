"""
Mini-Razorpay MCP server. Exposes Mini-Razorpay's payment/customer/reminder/
payment-link/settlement API as MCP tools over stdio (mirrors Sugam AI OS's
mcp-servers/playground-mcp/server.py: FastMCP, one server process per
external application, schema inferred purely from type hints + docstring).

Every tool takes an `access_token` argument — the caller's per-merchant JWT,
threaded in by the gateway's MCP client, never something the LLM invents or
sees as a "credential" to reason about; it's just an opaque string argument
it's told to pass through untouched.

Two Mini-Razorpay endpoints (send_payment_reminder, create_payment_link) can
legitimately come back "ambiguous" (409 AMBIGUOUS_PAYMENT / AMBIGUOUS_CUSTOMER)
— that is Mini-Razorpay correctly refusing to guess, not a failure. Those
tools return {"ambiguous": true, "candidates": [...]} instead of raising, so
the gateway's orchestrator (not this server, and never the LLM) can turn that
into a clarifying question.
"""

import os
import sys

from mcp.server.mcpserver import MCPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mini_razorpay_client import MiniRazorpayClient, MiniRazorpayAPIError  # noqa: E402

mcp = MCPServer("mini-razorpay-mcp")
client = MiniRazorpayClient()


def _error(e: MiniRazorpayAPIError) -> dict:
    return {"success": False, "error": True, "code": e.code, "message": e.message}


def _missing_token_error() -> dict:
    return {"success": False, "error": True, "code": "MISSING_TOKEN", "message": "No access token was provided for this call."}


# ---------------------------------------------------------------------------
# Payments (read)
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_payments(
    customer_name: str | None = None,
    customer_id: str | None = None,
    amount: float | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    access_token: str | None = None,
) -> dict:
    """Search the merchant's payments by any combination of customer name,
    customer id, exact amount, status (pending|paid|failed|expired), and a
    createdAt date range (ISO date strings). All fields are optional and
    combined with AND. Returns every match — never a single "best guess" —
    so use this to answer "find X" questions or to see how many payments
    match before deciding what to do next. Does not itself resolve
    ambiguity: if the result has more than one item and the user's request
    implied a single specific payment, ask the user which one they mean
    rather than picking one."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.search_payments(
            access_token,
            customerName=customer_name,
            customerId=customer_id,
            amount=amount,
            status=status,
            dateFrom=date_from,
            dateTo=date_to,
        )
        return {"success": True, **result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def get_payment(payment_id: str, access_token: str | None = None) -> dict:
    """Get full details of one payment by its exact paymentId (e.g. from a
    prior search_payments result)."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_payment(access_token, payment_id)
        return {"success": True, "payment": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def get_payment_status(payment_id: str, access_token: str | None = None) -> dict:
    """Lightweight status-only lookup for one payment by its exact
    paymentId: status, amount, dueDate, paidAt."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_payment_status(access_token, payment_id)
        return {"success": True, "payment": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def get_pending_payments(access_token: str | None = None) -> dict:
    """List every pending payment for the merchant, sorted by due date."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_pending_payments(access_token)
        return {"success": True, "items": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def get_pending_payments_priority(access_token: str | None = None) -> dict:
    """List pending payments with a deterministic 0-100 collection-priority
    score (based on how overdue it is, its amount, and the customer's
    payment history), sorted highest priority first. Use this when the user
    asks "who should I follow up with first" or similar prioritization
    questions."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_pending_payments_priority(access_token)
        return {"success": True, "items": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def get_overdue_payments(access_token: str | None = None) -> dict:
    """List pending payments whose due date has already passed."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_overdue_payments(access_token)
        return {"success": True, "items": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def get_payments_summary(access_token: str | None = None) -> dict:
    """Aggregate totals for the merchant's payments: totalPayments,
    totalAmount, counts/amounts per status (pending/paid/failed/expired),
    and overdueCount. Use this for "how much do I have pending" / "give me
    a summary" style questions rather than fetching every payment."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_payments_summary(access_token)
        return {"success": True, "summary": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


# ---------------------------------------------------------------------------
# Payments (write)
# ---------------------------------------------------------------------------


@mcp.tool()
async def update_payment_status(payment_id: str, status: str, access_token: str | None = None) -> dict:
    """Change a payment's status. status must be one of: pending, paid,
    failed, expired. Only certain transitions are allowed (e.g. a paid
    payment can never change again) — if the transition is invalid this
    returns a clear error explaining why, which should be relayed to the
    user as-is rather than retried with a different status."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.update_payment_status(access_token, payment_id, status)
        return {"success": True, "payment": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------


@mcp.tool()
async def send_payment_reminder(
    payment_id: str | None = None,
    customer_name: str | None = None,
    customer_id: str | None = None,
    amount: float | None = None,
    idempotency_key: str | None = None,
    access_token: str | None = None,
) -> dict:
    """Send a payment reminder for one specific pending payment. Prefer
    payment_id if it's already known (e.g. from a prior search_payments
    call or from a candidate the user just picked). Otherwise identify the
    payment by customer_name (or customer_id) AND amount — both together,
    since a name or amount alone is often not enough to pick exactly one
    payment. If more than one payment matches, this returns
    {"ambiguous": true, "candidates": [...]} instead of guessing — do not
    call this again with a modified guess; instead ask the user which
    candidate they mean and retry with that candidate's exact paymentId.
    If a reminder was already sent for this payment in the last 24 hours,
    this returns {"duplicate": true, ...} instead of sending a second one."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.send_reminder(
            access_token,
            payment_id=payment_id,
            customer_id=customer_id,
            customer_name=customer_name,
            amount=amount,
            idempotency_key=idempotency_key,
        )
        if result.get("ambiguous"):
            return result
        if result.get("duplicate"):
            return result
        return {"success": True, "reminder": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


# ---------------------------------------------------------------------------
# Payment Links
# ---------------------------------------------------------------------------


@mcp.tool()
async def create_payment_link(
    amount: float | None = None,
    customer_id: str | None = None,
    customer_name: str | None = None,
    description: str | None = None,
    expires_at: str | None = None,
    idempotency_key: str | None = None,
    existing_payment_id: str | None = None,
    access_token: str | None = None,
) -> dict:
    """Create a shareable payment link for a specific customer and amount
    (amount must be a positive number in INR unless existing_payment_id is
    given, in which case the amount/description are taken from that
    existing payment and must not be supplied). Identify the customer by
    customer_id (exact) or customer_name (matched by the backend). If the
    name matches more than one customer, this returns
    {"ambiguous": true, "candidates": [...]} instead of guessing — ask the
    user which customer they mean and retry with that candidate's exact
    customer_id. On success, returns the real paymentLinkId and a real,
    working shortUrl the customer can actually pay through — never invent
    or reconstruct a link URL yourself, always relay exactly the shortUrl
    this tool returns."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.create_payment_link(
            access_token,
            customer_id=customer_id,
            customer_name=customer_name,
            amount=amount,
            description=description,
            expires_at=expires_at,
            idempotency_key=idempotency_key,
            existing_payment_id=existing_payment_id,
        )
        if result.get("ambiguous"):
            return result
        return {"success": True, "payment_link": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_customers(search: str, access_token: str | None = None) -> dict:
    """Search the merchant's customers by a name, phone, or company
    substring. Use this to look up a customer before creating a payment
    link or reminder if you're not sure the name is unique, or to answer
    "do I have a customer named X" style questions."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.search_customers(access_token, search)
        return {"success": True, "items": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def get_customer(customer_id: str, access_token: str | None = None) -> dict:
    """Get full details of one customer by their exact customerId (e.g.
    from a prior search_customers result), including their phone number,
    email, and lifetime paid/pending payment totals."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_customer(access_token, customer_id)
        return {"success": True, "customer": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


# ---------------------------------------------------------------------------
# Settlements
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_settlements_summary(access_token: str | None = None) -> dict:
    """List the merchant's settlements and a summary
    (totalSettled, pendingSettlement, latestSettlement). Use this for
    "when do I get paid out" / "how much has settled" style questions."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_settlements(access_token)
        return {"success": True, **result}
    except MiniRazorpayAPIError as e:
        return _error(e)


if __name__ == "__main__":
    mcp.run()
