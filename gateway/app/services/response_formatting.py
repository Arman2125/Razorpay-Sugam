"""
Template-based reply formatting — mirrors Sugam AI OS's response_formatting.py
/ reply_formatter.py split: deterministic, per-tool templates for normal
success/error/ambiguity outcomes; an LLM is never used to phrase a result
that's already fully known (only the narrow, separately-gated fallback in
llm_fallback_formatter.py touches the LLM, and only for genuinely
unclassified failures).
"""

MAX_LIST_ITEMS = 10


def _fmt_amount(amount) -> str:
    try:
        return f"₹{amount:,.0f}"
    except (TypeError, ValueError):
        return f"₹{amount}"


def _fmt_date(iso_str: str | None) -> str:
    if not iso_str:
        return "—"
    return iso_str[:10]


def format_clarification(tool_name: str, candidates: list[dict]) -> str:
    lines = []
    if tool_name == "send_payment_reminder":
        lines.append(f"I found {len(candidates)} matching payments:")
        for i, c in enumerate(candidates[:MAX_LIST_ITEMS], start=1):
            lines.append(f"{i}. {_fmt_amount(c.get('amount'))} — due {_fmt_date(c.get('dueDate'))} — {c.get('status')}")
    elif tool_name in {"create_payment_link", "create_order", "create_invoice", "create_subscription"}:
        lines.append(f"I found {len(candidates)} matching customers:")
        for i, c in enumerate(candidates[:MAX_LIST_ITEMS], start=1):
            company = f" ({c['company']})" if c.get("company") else ""
            lines.append(f"{i}. {c.get('name')}{company} — {c.get('phone')}")
    else:
        lines.append(f"I found {len(candidates)} matches:")
        for i, c in enumerate(candidates[:MAX_LIST_ITEMS], start=1):
            lines.append(f"{i}. {c}")

    lines.append("\nReply with the number of the one you mean.")
    return "\n".join(lines)


def format_reprompt() -> str:
    return "Sorry, I didn't catch that — please reply with just the number, e.g. 1."


def format_reprompt_expired() -> str:
    return "That request has expired — please ask again."


def format_declined_unregistered() -> str:
    return "This number isn't registered with any Mini-Razorpay merchant, so I can't help with that here."


def format_known_error(code: str, message: str) -> str:
    friendly = {
        "DUPLICATE_REMINDER": "A reminder for this payment was already sent in the last 24 hours, so I didn't send another one.",
        "INVALID_TRANSITION": f"I couldn't make that change: {message}",
        "INVALID_STATUS": f"That's not a valid status: {message}",
        "INVALID_AMOUNT": "The amount needs to be a positive number.",
        "PAYMENT_NOT_FOUND": "I couldn't find a payment matching that.",
        "CUSTOMER_NOT_FOUND": "I couldn't find a customer matching that.",
        "NO_STATE_CHANGE": "That's already its current status, so there's nothing to change.",
        "MISSING_PAYMENT_ID": "I need the payment this is for — please give me a paymentId or enough details to find it.",
        "MISSING_AMOUNT": "I need an amount to do that.",
        "MISSING_CUSTOMER_IDENTIFIER": "I need to know which customer this is for.",
        "MISSING_INTERVAL": "I need a billing interval (day, week, month, or year) to create a subscription.",
        "INVALID_INTERVAL": "The billing interval must be one of: day, week, month, year.",
        "PAYMENT_NOT_PAID": f"That payment isn't in a paid state: {message}",
        "REFUND_EXCEEDS_BALANCE": f"I can't refund that much: {message}",
        "REFUND_NOT_FOUND": "I couldn't find a refund matching that.",
        "ORDER_NOT_FOUND": "I couldn't find an order matching that.",
        "INVOICE_NOT_FOUND": "I couldn't find an invoice matching that.",
        "INVOICE_NOT_DRAFT": f"I couldn't edit that invoice: {message}",
        "SUBSCRIPTION_NOT_FOUND": "I couldn't find a subscription matching that.",
        "SETTLEMENT_NOT_FOUND": "I couldn't find a settlement matching that.",
        "ALREADY_PAID": "That's already been paid, so there's nothing more to do.",
        "LINK_NOT_ACTIVE": f"That payment link isn't active anymore: {message}",
        "PENDING_PAYMENT_NOT_FOUND": "I couldn't find a matching pending payment to link that to.",
    }
    return friendly.get(code, message)


def format_tool_success(tool_name: str, result: dict) -> str:
    if tool_name == "get_payments_summary":
        s = result["summary"]
        return (
            f"Payments summary:\n"
            f"Total: {s['totalPayments']} payments, {_fmt_amount(s['totalAmount'])}\n"
            f"Pending: {s['pendingCount']} ({_fmt_amount(s['pendingAmount'])})\n"
            f"Paid: {s['paidCount']} ({_fmt_amount(s['paidAmount'])})\n"
            f"Overdue: {s['overdueCount']}"
        )

    if tool_name in {"get_pending_payments", "get_overdue_payments", "get_pending_payments_priority"}:
        items = result.get("items", [])
        if not items:
            return "No matching payments right now."
        lines = [f"Found {len(items)} payment(s):"]
        for p in items[:MAX_LIST_ITEMS]:
            name = (p.get("customer") or {}).get("name", "Unknown")
            lines.append(f"- {name}: {_fmt_amount(p.get('amount'))}, due {_fmt_date(p.get('dueDate'))}")
        if len(items) > MAX_LIST_ITEMS:
            lines.append(f"...and {len(items) - MAX_LIST_ITEMS} more.")
        return "\n".join(lines)

    if tool_name == "search_payments" or tool_name == "search_customers":
        items = result.get("items", [])
        if not items:
            return "No matches found."
        return f"Found {len(items)} match(es)."

    if tool_name == "get_payment" or tool_name == "get_payment_status":
        p = result.get("payment", {})
        return f"Payment {p.get('paymentId')}: {p.get('status')}, {_fmt_amount(p.get('amount'))}"

    if tool_name == "update_payment_status":
        p = result.get("payment", {})
        return f"Payment {p.get('paymentId')} is now {p.get('status')}."

    if tool_name == "send_payment_reminder":
        return "Reminder sent."

    if tool_name == "create_payment_link":
        link = result.get("payment_link", {})
        return f"Payment link created for {_fmt_amount(link.get('amount'))}: {link.get('shortUrl')}"

    if tool_name == "get_settlements_summary":
        s = result.get("summary", {})
        return (
            f"Settlements: {_fmt_amount(s.get('totalSettled', 0))} settled, "
            f"{_fmt_amount(s.get('pendingSettlement', 0))} pending."
        )

    if tool_name == "get_settlement":
        s = result.get("settlement", {})
        return f"Settlement {s.get('settlementId')}: {_fmt_amount(s.get('amount'))}, {s.get('status')}."

    if tool_name == "create_refund":
        r = result.get("refund", {})
        return f"Refunded {_fmt_amount(r.get('amount'))} for payment {r.get('paymentId')}."

    if tool_name == "get_refund":
        r = result.get("refund", {})
        return f"Refund {r.get('refundId')}: {_fmt_amount(r.get('amount'))} for payment {r.get('paymentId')}, {r.get('status')}."

    if tool_name in {"get_refunds", "get_payment_refunds"}:
        items = result.get("items", [])
        if not items:
            return "No matching refunds found."
        total = sum(r.get("amount", 0) for r in items)
        return f"Found {len(items)} refund(s) totaling {_fmt_amount(total)}."

    if tool_name == "get_refundable_amount":
        return (
            f"Payment {result.get('paymentId')}: {_fmt_amount(result.get('refundableAmount', 0))} "
            f"still refundable out of {_fmt_amount(result.get('paymentAmount', 0))}."
        )

    if tool_name == "create_order":
        o = result.get("order", {})
        return f"Order {o.get('orderId')} created for {_fmt_amount(o.get('amount'))}."

    if tool_name == "get_order":
        o = result.get("order", {})
        return f"Order {o.get('orderId')}: {_fmt_amount(o.get('amount'))}, {o.get('status')}."

    if tool_name == "get_orders":
        items = result.get("items", [])
        if not items:
            return "No matching orders found."
        return f"Found {len(items)} order(s)."

    if tool_name == "update_order_status":
        o = result.get("order", {})
        return f"Order {o.get('orderId')} is now {o.get('status')}."

    if tool_name == "create_invoice":
        inv = result.get("invoice", {})
        return f"Invoice {inv.get('invoiceId')} created (draft) for {_fmt_amount(inv.get('amount'))}."

    if tool_name in {"get_invoice", "update_invoice", "issue_invoice", "mark_invoice_paid", "cancel_invoice"}:
        inv = result.get("invoice", {})
        return f"Invoice {inv.get('invoiceId')}: {_fmt_amount(inv.get('amount'))}, {inv.get('status')}."

    if tool_name == "get_invoices":
        items = result.get("items", [])
        if not items:
            return "No matching invoices found."
        return f"Found {len(items)} invoice(s)."

    if tool_name == "create_subscription":
        s = result.get("subscription", {})
        return (
            f"Subscription {s.get('subscriptionId')} created: {_fmt_amount(s.get('amount'))} "
            f"every {s.get('intervalCount', 1)} {s.get('interval')}(s), status {s.get('status')}."
        )

    if tool_name in {"get_subscription", "pause_subscription", "resume_subscription", "cancel_subscription"}:
        s = result.get("subscription", {})
        return f"Subscription {s.get('subscriptionId')}: {s.get('status')}, next billing {_fmt_date(s.get('nextBillingAt'))}."

    if tool_name == "get_subscriptions":
        items = result.get("items", [])
        if not items:
            return "No matching subscriptions found."
        return f"Found {len(items)} subscription(s)."

    if tool_name == "process_due_subscriptions":
        processed = result.get("processed", 0)
        billed = sum(1 for r in result.get("results", []) if r.get("billed"))
        return f"Processed {processed} due subscription(s) — {billed} billed."

    if tool_name == "get_analytics":
        a = result.get("analytics", {})
        overview = a.get("overview", {})
        return (
            f"Analytics: {_fmt_amount(overview.get('totalVolume', 0))} total volume across "
            f"{overview.get('totalPayments', 0)} payments; {_fmt_amount(overview.get('pendingAmount', 0))} pending."
        )

    if tool_name == "get_activity":
        items = result.get("items", [])
        if not items:
            return "No matching activity found."
        return f"Found {len(items)} activity record(s)."

    if tool_name == "get_payment_links":
        items = result.get("items", [])
        if not items:
            return "No matching payment links found."
        return f"Found {len(items)} payment link(s)."

    if tool_name in {"get_payment_link", "cancel_payment_link"}:
        link = result.get("payment_link", {})
        return f"Payment link {link.get('paymentLinkId')}: {_fmt_amount(link.get('amount'))}, {link.get('status')}."

    return "Your request was completed successfully."
