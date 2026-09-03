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
    elif tool_name == "create_payment_link":
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

    return "Your request was completed successfully."
