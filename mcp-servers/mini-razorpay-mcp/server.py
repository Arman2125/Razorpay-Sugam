"""
Mini-Razorpay MCP server. Exposes Mini-Razorpay's payment/customer/reminder/
payment-link/settlement/refund/order/invoice/subscription/analytics/activity
API as MCP tools over stdio (mirrors Sugam AI OS's
mcp-servers/playground-mcp/server.py: FastMCP, one server process per
external application, schema inferred purely from type hints + docstring).

Every tool takes an `access_token` argument — the caller's per-merchant JWT,
threaded in by the gateway's MCP client, never something the LLM invents or
sees as a "credential" to reason about; it's just an opaque string argument
it's told to pass through untouched. Mini-Razorpay's own JWT-derived
merchantId is the only merchant-scoping mechanism anywhere in this file —
no tool accepts a merchantId argument, and the LLM is never able to select
a different merchant.

Several endpoints that resolve a customer by name (send_payment_reminder,
create_payment_link, create_order, create_invoice, create_subscription) can
legitimately come back "ambiguous" (409 AMBIGUOUS_PAYMENT / AMBIGUOUS_CUSTOMER)
— that is Mini-Razorpay correctly refusing to guess, not a failure. Those
tools return {"ambiguous": true, "candidates": [...]} instead of raising, so
the gateway's orchestrator (not this server, and never the LLM) can turn that
into a clarifying question.

Mini-Razorpay has no in-process recurring-billing scheduler: a subscription
is only ever billed when process_due_subscriptions below is explicitly
invoked (see that tool's own docstring). This server does not, and must
not, claim or simulate background billing.
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
    min_amount: float | None = None,
    max_amount: float | None = None,
    status: str | None = None,
    payment_method: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort_by: str | None = None,
    sort_order: str | None = None,
    page: int | None = None,
    limit: int | None = None,
    access_token: str | None = None,
) -> dict:
    """Search the merchant's payments by any combination of customer name,
    customer id, exact amount, an amount range (min_amount/max_amount),
    status (pending|paid|failed|expired), paymentMethod
    (UPI|Card|Net Banking|Cash), and a createdAt date range (ISO date
    strings). All fields are optional and combined with AND. sort_by (one
    of amount|createdAt|dueDate) and sort_order (asc|desc) control ordering;
    page/limit optionally paginate large result sets. Returns every match —
    never a single "best guess" — so use this to answer "find X" questions
    or to see how many payments match before deciding what to do next. Does
    not itself resolve ambiguity: if the result has more than one item and
    the user's request implied a single specific payment, ask the user
    which one they mean rather than picking one."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.search_payments(
            access_token,
            customerName=customer_name,
            customerId=customer_id,
            amount=amount,
            minAmount=min_amount,
            maxAmount=max_amount,
            status=status,
            paymentMethod=payment_method,
            dateFrom=date_from,
            dateTo=date_to,
            sortBy=sort_by,
            sortOrder=sort_order,
            page=page,
            limit=limit,
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
async def get_settlements_summary(
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    access_token: str | None = None,
) -> dict:
    """List the merchant's settlements and a summary
    (totalSettled, pendingSettlement, failedSettlement, latestSettlement).
    Optionally filter the listed items by status (processed|pending|failed)
    or a settlementDate range — note the summary totals are always computed
    over the merchant's ENTIRE settlement history regardless of these
    filters, never just the filtered/returned items. Use this for "when do
    I get paid out" / "how much has settled" style questions."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_settlements(access_token, status=status, date_from=date_from, date_to=date_to)
        return {"success": True, **result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def get_settlement(settlement_id: str, access_token: str | None = None) -> dict:
    """Get full details of one settlement by its exact settlementId."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_settlement(access_token, settlement_id)
        return {"success": True, "settlement": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


# ---------------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------------


@mcp.tool()
async def create_refund(
    payment_id: str,
    amount: float,
    reason: str | None = None,
    idempotency_key: str | None = None,
    access_token: str | None = None,
) -> dict:
    """Refund part or all of one already-paid payment, identified by its
    exact paymentId. amount must be a positive number, and Mini-Razorpay is
    the sole authority on how much of that payment is still refundable — it
    will reject an amount exceeding the remaining refundable balance with
    REFUND_EXCEEDS_BALANCE rather than partially applying it, so never
    compute or assume a refundable balance yourself; call
    get_refundable_amount first if you're not sure. A refund never changes
    the underlying payment's own status (it stays "paid")."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.create_refund(
            access_token, payment_id=payment_id, amount=amount, reason=reason, idempotency_key=idempotency_key
        )
        return {"success": True, "refund": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def get_refund(refund_id: str, access_token: str | None = None) -> dict:
    """Get full details of one refund by its exact refundId."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_refund(access_token, refund_id)
        return {"success": True, "refund": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def get_refunds(
    status: str | None = None,
    payment_id: str | None = None,
    customer_id: str | None = None,
    page: int | None = None,
    limit: int | None = None,
    access_token: str | None = None,
) -> dict:
    """List the merchant's refunds, optionally filtered by status, exact
    paymentId, or exact customerId, with optional page/limit pagination."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_refunds(
            access_token, status=status, payment_id=payment_id, customer_id=customer_id, page=page, limit=limit
        )
        return {"success": True, **result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def get_payment_refunds(payment_id: str, access_token: str | None = None) -> dict:
    """List every refund already made against one specific payment, by its
    exact paymentId."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_payment_refunds(access_token, payment_id)
        return {"success": True, "items": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def get_refundable_amount(payment_id: str, access_token: str | None = None) -> dict:
    """Get exactly how much of one payment (by its exact paymentId) is still
    refundable: paymentAmount, paymentStatus, refundedAmount so far, and the
    remaining refundableAmount. Always rely on this figure — or on
    create_refund's own rejection — rather than computing a refundable
    balance yourself."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_refundable_amount(access_token, payment_id)
        return {"success": True, **result}
    except MiniRazorpayAPIError as e:
        return _error(e)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


@mcp.tool()
async def create_order(
    amount: float,
    customer_id: str | None = None,
    customer_name: str | None = None,
    currency: str | None = None,
    receipt: str | None = None,
    notes: dict | None = None,
    idempotency_key: str | None = None,
    access_token: str | None = None,
) -> dict:
    """Create a new order for a specific customer and a positive amount.
    Identify the customer by customer_id (exact) or customer_name (matched
    by the backend) — one of the two is required. If customer_name matches
    more than one customer, this returns
    {"ambiguous": true, "candidates": [...]} instead of guessing — ask the
    user which customer they mean and retry with that candidate's exact
    customer_id. currency defaults to INR if omitted. A new order always
    starts in status "created"."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.create_order(
            access_token,
            amount=amount,
            customer_id=customer_id,
            customer_name=customer_name,
            currency=currency,
            receipt=receipt,
            notes=notes,
            idempotency_key=idempotency_key,
        )
        if result.get("ambiguous"):
            return result
        return {"success": True, "order": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def get_order(order_id: str, access_token: str | None = None) -> dict:
    """Get full details of one order by its exact orderId."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_order(access_token, order_id)
        return {"success": True, "order": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def get_orders(
    status: str | None = None,
    customer_id: str | None = None,
    page: int | None = None,
    limit: int | None = None,
    access_token: str | None = None,
) -> dict:
    """List the merchant's orders, optionally filtered by status
    (created|attempted|paid|cancelled) or exact customerId, with optional
    page/limit pagination."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_orders(access_token, status=status, customer_id=customer_id, page=page, limit=limit)
        return {"success": True, **result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def update_order_status(
    order_id: str,
    status: str,
    payment_id: str | None = None,
    access_token: str | None = None,
) -> dict:
    """Change an order's status. status must be one of: attempted, paid,
    cancelled (an order can never be moved back to "created", and a paid or
    cancelled order can never change again). Only certain transitions are
    allowed from the order's current status — an invalid transition returns
    a clear error to relay to the user as-is, never retry with a different
    guess. When moving to "paid", you may optionally supply payment_id (an
    existing paid Payment belonging to the same customer) to link the order
    to it."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.update_order_status(access_token, order_id, status, payment_id=payment_id)
        return {"success": True, "order": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


@mcp.tool()
async def create_invoice(
    amount: float,
    customer_id: str | None = None,
    customer_name: str | None = None,
    order_id: str | None = None,
    currency: str | None = None,
    description: str | None = None,
    due_date: str | None = None,
    idempotency_key: str | None = None,
    access_token: str | None = None,
) -> dict:
    """Create a new invoice — always starts in status "draft" — for a
    specific customer and a positive amount. Identify the customer by
    customer_id (exact) or customer_name (matched by the backend) — one of
    the two is required. If customer_name matches more than one customer,
    this returns {"ambiguous": true, "candidates": [...]} instead of
    guessing — ask the user which customer they mean and retry with that
    candidate's exact customer_id. order_id optionally links it to an
    existing order. due_date is an ISO date string."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.create_invoice(
            access_token,
            amount=amount,
            customer_id=customer_id,
            customer_name=customer_name,
            order_id=order_id,
            currency=currency,
            description=description,
            due_date=due_date,
            idempotency_key=idempotency_key,
        )
        if result.get("ambiguous"):
            return result
        return {"success": True, "invoice": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def get_invoice(invoice_id: str, access_token: str | None = None) -> dict:
    """Get full details of one invoice by its exact invoiceId. A stale
    "issued" invoice whose due date has already passed is returned as
    "overdue" rather than "issued"."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_invoice(access_token, invoice_id)
        return {"success": True, "invoice": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def get_invoices(
    status: str | None = None,
    customer_id: str | None = None,
    page: int | None = None,
    limit: int | None = None,
    access_token: str | None = None,
) -> dict:
    """List the merchant's invoices, optionally filtered by status
    (draft|issued|paid|overdue|cancelled) or exact customerId, with optional
    page/limit pagination."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_invoices(
            access_token, status=status, customer_id=customer_id, page=page, limit=limit
        )
        return {"success": True, **result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def update_invoice(
    invoice_id: str,
    amount: float | None = None,
    description: str | None = None,
    due_date: str | None = None,
    access_token: str | None = None,
) -> dict:
    """Edit an invoice's amount, description, and/or due date. Only allowed
    while the invoice is still in status "draft" — once issued its fields
    are frozen and this returns INVOICE_NOT_DRAFT. Only include the fields
    you actually want to change."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.update_invoice_fields(
            access_token, invoice_id, amount=amount, description=description, due_date=due_date
        )
        return {"success": True, "invoice": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def issue_invoice(invoice_id: str, access_token: str | None = None) -> dict:
    """Issue a draft invoice, moving it from "draft" to "issued" and locking
    its fields against further edits. Only allowed from status "draft"."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.update_invoice_status(access_token, invoice_id, "issued")
        return {"success": True, "invoice": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def mark_invoice_paid(
    invoice_id: str, payment_id: str | None = None, access_token: str | None = None
) -> dict:
    """Mark an invoice as paid. Only allowed from status "issued" or
    "overdue". You may optionally supply payment_id (an existing paid
    Payment belonging to the same customer) to link the invoice to it."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.update_invoice_status(access_token, invoice_id, "paid", payment_id=payment_id)
        return {"success": True, "invoice": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def cancel_invoice(invoice_id: str, access_token: str | None = None) -> dict:
    """Cancel an invoice. Only allowed from status "draft", "issued", or
    "overdue" — a paid invoice can never be cancelled."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.update_invoice_status(access_token, invoice_id, "cancelled")
        return {"success": True, "invoice": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


@mcp.tool()
async def create_subscription(
    amount: float,
    interval: str,
    customer_id: str | None = None,
    customer_name: str | None = None,
    plan_id: str | None = None,
    currency: str | None = None,
    interval_count: int | None = None,
    start_at: str | None = None,
    idempotency_key: str | None = None,
    access_token: str | None = None,
) -> dict:
    """Create a recurring subscription for a specific customer. Identify the
    customer by customer_id (exact) or customer_name (matched by the
    backend) — one of the two is required; if customer_name matches more
    than one customer this returns
    {"ambiguous": true, "candidates": [...]} instead of guessing — ask the
    user which customer they mean and retry with that candidate's exact
    customer_id. interval must be one of: day, week, month, year.
    interval_count (default 1) is how many of that interval per billing
    cycle. start_at (ISO date string, defaults to now) is when billing
    begins — the subscription starts "active" immediately if start_at is
    now or in the past, or "created" if start_at is in the future. Creating
    a subscription never itself charges anything: a billing charge only
    happens later when process_due_subscriptions is explicitly invoked and
    this subscription's next billing date has arrived."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.create_subscription(
            access_token,
            amount=amount,
            interval=interval,
            customer_id=customer_id,
            customer_name=customer_name,
            plan_id=plan_id,
            currency=currency,
            interval_count=interval_count,
            start_at=start_at,
            idempotency_key=idempotency_key,
        )
        if result.get("ambiguous"):
            return result
        return {"success": True, "subscription": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def get_subscription(subscription_id: str, access_token: str | None = None) -> dict:
    """Get full details of one subscription by its exact subscriptionId,
    including its current status and nextBillingAt. Read-only — never
    triggers billing."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_subscription(access_token, subscription_id)
        return {"success": True, "subscription": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def get_subscriptions(
    status: str | None = None,
    customer_id: str | None = None,
    page: int | None = None,
    limit: int | None = None,
    access_token: str | None = None,
) -> dict:
    """List the merchant's subscriptions, optionally filtered by status
    (created|active|paused|cancelled|completed) or exact customerId, with
    optional page/limit pagination. Read-only — never triggers billing. Use
    this freely to answer "show me subscriptions due" or similar questions
    without ever processing anything; a due subscription is only ever
    actually billed by explicitly calling process_due_subscriptions."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_subscriptions(
            access_token, status=status, customer_id=customer_id, page=page, limit=limit
        )
        return {"success": True, **result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def pause_subscription(subscription_id: str, access_token: str | None = None) -> dict:
    """Pause an active subscription, stopping future billing until resumed.
    Only allowed from status "active"."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.update_subscription_status(access_token, subscription_id, "paused")
        return {"success": True, "subscription": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def resume_subscription(subscription_id: str, access_token: str | None = None) -> dict:
    """Resume a paused subscription. Only allowed from status "paused". If
    its next billing date had already passed while paused, it is reset to
    now — resuming never bills for time that was missed while paused."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.update_subscription_status(access_token, subscription_id, "active")
        return {"success": True, "subscription": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def cancel_subscription(
    subscription_id: str, at_cycle_end: bool | None = None, access_token: str | None = None
) -> dict:
    """Cancel a subscription. By default this cancels it immediately. Pass
    at_cycle_end=true to instead keep it billing through its current cycle
    and only stop it at its next billing date."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.update_subscription_status(
            access_token, subscription_id, "cancelled", at_cycle_end=at_cycle_end
        )
        return {"success": True, "subscription": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def process_due_subscriptions(access_token: str | None = None) -> dict:
    """Explicitly run the merchant's due-subscription billing pass RIGHT
    NOW: every one of this merchant's active/created subscriptions whose
    nextBillingAt has arrived is billed exactly once (or cancelled, if it
    was scheduled to cancel at this cycle), and its nextBillingAt advances.
    Safe to call repeatedly — an already-processed cycle is never billed
    twice. Mini-Razorpay has no background scheduler of its own: a
    subscription's cycle is only ever billed when this is explicitly
    invoked. Do NOT call this just because the user asked to see
    subscriptions, their status, or what is due — only call it when the
    user clearly and explicitly asks to process, run, or bill the due
    subscriptions now."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.process_due_subscriptions(access_token)
        return {"success": True, **result}
    except MiniRazorpayAPIError as e:
        return _error(e)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_analytics(access_token: str | None = None) -> dict:
    """Get the merchant's full analytics summary in one call: payment
    overview and status/method breakdowns, volume over time, refund totals,
    order totals, invoice totals (including outstanding/overdue amounts),
    payment-link conversion, and settlement totals. Prefer this over
    combining many smaller reads when the user asks a broad "how's my
    business doing" style question. Every figure comes directly from
    Mini-Razorpay — never recompute, estimate, or cross-check it yourself."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_analytics_summary(access_token)
        return {"success": True, "analytics": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


# ---------------------------------------------------------------------------
# Activity / Audit
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_activity(
    action: str | None = None,
    entity_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int | None = None,
    limit: int | None = None,
    access_token: str | None = None,
) -> dict:
    """List the merchant's audit trail of everything that has happened
    (payments, refunds, orders, invoices, subscriptions, settlements,
    reminders, payment links, customers), optionally filtered by an exact
    action name, entity_type (payment|customer|reminder|settlement|
    paymentLink|refund|order|invoice|subscription), or a date range, with
    optional page/limit pagination."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_activity(
            access_token,
            action=action,
            entity_type=entity_type,
            date_from=date_from,
            date_to=date_to,
            page=page,
            limit=limit,
        )
        return {"success": True, **result}
    except MiniRazorpayAPIError as e:
        return _error(e)


# ---------------------------------------------------------------------------
# Payment Links (list / get / cancel)
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_payment_links(
    status: str | None = None, customer_id: str | None = None, access_token: str | None = None
) -> dict:
    """List the merchant's payment links, optionally filtered by status
    (active|paid|cancelled|expired) or exact customerId."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_payment_links(access_token, status=status, customer_id=customer_id)
        return {"success": True, "items": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def get_payment_link(payment_link_id: str, access_token: str | None = None) -> dict:
    """Get full details of one payment link by its exact paymentLinkId,
    including its real shortUrl and current status."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.get_payment_link(access_token, payment_link_id)
        return {"success": True, "payment_link": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


@mcp.tool()
async def cancel_payment_link(payment_link_id: str, access_token: str | None = None) -> dict:
    """Cancel an active payment link so it can no longer be paid. Only
    allowed while the link is "active" — a link that's already paid,
    cancelled, or expired can never change again."""
    if not access_token:
        return _missing_token_error()
    try:
        result = await client.update_payment_link_status(access_token, payment_link_id, "cancelled")
        return {"success": True, "payment_link": result}
    except MiniRazorpayAPIError as e:
        return _error(e)


if __name__ == "__main__":
    mcp.run()
