"""
Tool-level tests for server.py's new MCP tools. `@mcp.tool()` returns the
undecorated function unchanged (verified against the installed `mcp` SDK),
so every tool below is called directly as a plain coroutine — no MCP
transport needed. `server.client` (the single module-level
MiniRazorpayClient instance) is monkeypatched per-test so these tests never
make a real HTTP call and never need a live Mini-Razorpay backend.

Focus: the contract this integration cares most about — missing-token
short-circuiting, ambiguous-customer passthrough (never resolved here or by
an LLM), known-error passthrough, and that the three invoice
lifecycle-shortcut tools and three subscription lifecycle tools call the
underlying status endpoint with the exact fixed status they claim to.
"""

import inspect
from unittest.mock import AsyncMock

import pytest

import server as server_module
from mini_razorpay_client import MiniRazorpayAPIError

# Every MCP tool registered in server.py. Kept as an explicit literal list
# (rather than introspecting the FastMCP registry) so this test fails loudly
# if a newly added tool is forgotten here, forcing a deliberate decision
# about it rather than silently skipping coverage.
ALL_TOOL_NAMES = [
    "search_payments", "get_payment", "get_payment_status", "get_pending_payments",
    "get_pending_payments_priority", "get_overdue_payments", "get_payments_summary",
    "update_payment_status", "send_payment_reminder", "create_payment_link",
    "search_customers", "get_customer", "get_settlements_summary", "get_settlement",
    "create_refund", "get_refund", "get_refunds", "get_payment_refunds", "get_refundable_amount",
    "create_order", "get_order", "get_orders", "update_order_status",
    "create_invoice", "get_invoice", "get_invoices", "update_invoice", "issue_invoice",
    "mark_invoice_paid", "cancel_invoice",
    "create_subscription", "get_subscription", "get_subscriptions", "pause_subscription",
    "resume_subscription", "cancel_subscription", "process_due_subscriptions",
    "get_analytics", "get_activity",
    "get_payment_links", "get_payment_link", "cancel_payment_link",
]


def test_no_tool_ever_accepts_a_merchant_id_argument():
    """Section 18's hard rule: merchant identity must only ever come from
    the trusted server-side JWT (access_token) — no tool may expose a
    merchant_id/merchantId parameter an LLM could fill in and use to select
    a different merchant's data."""
    for name in ALL_TOOL_NAMES:
        fn = getattr(server_module, name)
        params = set(inspect.signature(fn).parameters)
        assert "merchant_id" not in params, f"{name} must never accept merchant_id"
        assert "merchantId" not in params, f"{name} must never accept merchantId"


def test_every_registered_tool_is_covered_by_the_missing_token_list():
    """Guards against silently forgetting to add a new tool to
    _NEW_TOOLS_WITH_MINIMAL_ARGS above (or to ALL_TOOL_NAMES here) when a
    future tool is added — every tool must take access_token as a keyword
    parameter with a None-able default."""
    for name in ALL_TOOL_NAMES:
        fn = getattr(server_module, name)
        sig = inspect.signature(fn)
        assert "access_token" in sig.parameters, f"{name} must accept access_token"
        assert sig.parameters["access_token"].default is None

MISSING_TOKEN = {
    "success": False,
    "error": True,
    "code": "MISSING_TOKEN",
    "message": "No access token was provided for this call.",
}

# (tool callable, kwargs excluding access_token) for every new write/read
# tool added by this integration, used to verify the universal
# "no access_token -> MISSING_TOKEN, never call the client" contract.
_NEW_TOOLS_WITH_MINIMAL_ARGS = [
    (server_module.create_refund, {"payment_id": "pay_1", "amount": 100}),
    (server_module.get_refund, {"refund_id": "rf_1"}),
    (server_module.get_refunds, {}),
    (server_module.get_payment_refunds, {"payment_id": "pay_1"}),
    (server_module.get_refundable_amount, {"payment_id": "pay_1"}),
    (server_module.create_order, {"amount": 100, "customer_id": "cust_1"}),
    (server_module.get_order, {"order_id": "order_1"}),
    (server_module.get_orders, {}),
    (server_module.update_order_status, {"order_id": "order_1", "status": "paid"}),
    (server_module.create_invoice, {"amount": 100, "customer_id": "cust_1"}),
    (server_module.get_invoice, {"invoice_id": "inv_1"}),
    (server_module.get_invoices, {}),
    (server_module.update_invoice, {"invoice_id": "inv_1", "amount": 200}),
    (server_module.issue_invoice, {"invoice_id": "inv_1"}),
    (server_module.mark_invoice_paid, {"invoice_id": "inv_1"}),
    (server_module.cancel_invoice, {"invoice_id": "inv_1"}),
    (server_module.create_subscription, {"amount": 100, "interval": "month", "customer_id": "cust_1"}),
    (server_module.get_subscription, {"subscription_id": "sub_1"}),
    (server_module.get_subscriptions, {}),
    (server_module.pause_subscription, {"subscription_id": "sub_1"}),
    (server_module.resume_subscription, {"subscription_id": "sub_1"}),
    (server_module.cancel_subscription, {"subscription_id": "sub_1"}),
    (server_module.process_due_subscriptions, {}),
    (server_module.get_analytics, {}),
    (server_module.get_activity, {}),
    (server_module.get_payment_links, {}),
    (server_module.get_payment_link, {"payment_link_id": "pl_1"}),
    (server_module.cancel_payment_link, {"payment_link_id": "pl_1"}),
    (server_module.get_settlement, {"settlement_id": "st_1"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, kwargs", _NEW_TOOLS_WITH_MINIMAL_ARGS, ids=[t.__name__ for t, _ in _NEW_TOOLS_WITH_MINIMAL_ARGS])
async def test_every_new_tool_requires_access_token(tool, kwargs):
    result = await tool(**kwargs, access_token=None)
    assert result == MISSING_TOKEN


# ---------------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_refund_success(monkeypatch):
    monkeypatch.setattr(server_module.client, "create_refund", AsyncMock(return_value={"refundId": "rf_1", "amount": 100}))
    result = await server_module.create_refund(payment_id="pay_1", amount=100, access_token="tok")
    assert result == {"success": True, "refund": {"refundId": "rf_1", "amount": 100}}
    server_module.client.create_refund.assert_awaited_once_with(
        "tok", payment_id="pay_1", amount=100, reason=None, idempotency_key=None
    )


@pytest.mark.asyncio
async def test_create_refund_exceeds_balance_returns_error_not_partial_success(monkeypatch):
    err = MiniRazorpayAPIError(400, "REFUND_EXCEEDS_BALANCE", "Cannot refund 999999; only 500 refundable")
    monkeypatch.setattr(server_module.client, "create_refund", AsyncMock(side_effect=err))
    result = await server_module.create_refund(payment_id="pay_1", amount=999999, access_token="tok")
    assert result == {
        "success": False,
        "error": True,
        "code": "REFUND_EXCEEDS_BALANCE",
        "message": "Cannot refund 999999; only 500 refundable",
    }


# ---------------------------------------------------------------------------
# Orders / Invoices / Subscriptions — ambiguous-customer passthrough
# ---------------------------------------------------------------------------


_AMBIGUOUS_CUSTOMER_RESULT = {
    "success": False,
    "ambiguous": True,
    "code": "AMBIGUOUS_CUSTOMER",
    "message": "Multiple customers match 'Rahul'",
    "candidates": [{"customerId": "c1", "name": "Rahul Sharma"}, {"customerId": "c2", "name": "Rahul Verma"}],
}


@pytest.mark.asyncio
async def test_create_order_ambiguous_customer_is_never_resolved_here(monkeypatch):
    monkeypatch.setattr(server_module.client, "create_order", AsyncMock(return_value=_AMBIGUOUS_CUSTOMER_RESULT))
    result = await server_module.create_order(amount=500, customer_name="Rahul", access_token="tok")
    assert result == _AMBIGUOUS_CUSTOMER_RESULT
    assert "order" not in result


@pytest.mark.asyncio
async def test_create_invoice_ambiguous_customer_is_never_resolved_here(monkeypatch):
    monkeypatch.setattr(server_module.client, "create_invoice", AsyncMock(return_value=_AMBIGUOUS_CUSTOMER_RESULT))
    result = await server_module.create_invoice(amount=500, customer_name="Rahul", access_token="tok")
    assert result == _AMBIGUOUS_CUSTOMER_RESULT


@pytest.mark.asyncio
async def test_create_subscription_ambiguous_customer_is_never_resolved_here(monkeypatch):
    monkeypatch.setattr(server_module.client, "create_subscription", AsyncMock(return_value=_AMBIGUOUS_CUSTOMER_RESULT))
    result = await server_module.create_subscription(
        amount=500, interval="month", customer_name="Rahul", access_token="tok"
    )
    assert result == _AMBIGUOUS_CUSTOMER_RESULT


# ---------------------------------------------------------------------------
# Invoice lifecycle shortcut tools call the right fixed status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_invoice_calls_status_issued(monkeypatch):
    mock = AsyncMock(return_value={"invoiceId": "inv_1", "status": "issued"})
    monkeypatch.setattr(server_module.client, "update_invoice_status", mock)
    await server_module.issue_invoice(invoice_id="inv_1", access_token="tok")
    mock.assert_awaited_once_with("tok", "inv_1", "issued")


@pytest.mark.asyncio
async def test_mark_invoice_paid_calls_status_paid_with_optional_payment_id(monkeypatch):
    mock = AsyncMock(return_value={"invoiceId": "inv_1", "status": "paid"})
    monkeypatch.setattr(server_module.client, "update_invoice_status", mock)
    await server_module.mark_invoice_paid(invoice_id="inv_1", payment_id="pay_9", access_token="tok")
    mock.assert_awaited_once_with("tok", "inv_1", "paid", payment_id="pay_9")


@pytest.mark.asyncio
async def test_cancel_invoice_calls_status_cancelled(monkeypatch):
    mock = AsyncMock(return_value={"invoiceId": "inv_1", "status": "cancelled"})
    monkeypatch.setattr(server_module.client, "update_invoice_status", mock)
    await server_module.cancel_invoice(invoice_id="inv_1", access_token="tok")
    mock.assert_awaited_once_with("tok", "inv_1", "cancelled")


@pytest.mark.asyncio
async def test_update_invoice_not_draft_error_surfaces(monkeypatch):
    err = MiniRazorpayAPIError(400, "INVOICE_NOT_DRAFT", "Cannot edit an invoice that is not in draft status")
    monkeypatch.setattr(server_module.client, "update_invoice_fields", AsyncMock(side_effect=err))
    result = await server_module.update_invoice(invoice_id="inv_1", amount=999, access_token="tok")
    assert result["error"] is True
    assert result["code"] == "INVOICE_NOT_DRAFT"


# ---------------------------------------------------------------------------
# Subscription lifecycle tools call the right fixed status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_subscription_calls_status_paused(monkeypatch):
    mock = AsyncMock(return_value={"subscriptionId": "sub_1", "status": "paused"})
    monkeypatch.setattr(server_module.client, "update_subscription_status", mock)
    await server_module.pause_subscription(subscription_id="sub_1", access_token="tok")
    mock.assert_awaited_once_with("tok", "sub_1", "paused")


@pytest.mark.asyncio
async def test_resume_subscription_calls_status_active(monkeypatch):
    mock = AsyncMock(return_value={"subscriptionId": "sub_1", "status": "active"})
    monkeypatch.setattr(server_module.client, "update_subscription_status", mock)
    await server_module.resume_subscription(subscription_id="sub_1", access_token="tok")
    mock.assert_awaited_once_with("tok", "sub_1", "active")


@pytest.mark.asyncio
async def test_cancel_subscription_passes_at_cycle_end(monkeypatch):
    mock = AsyncMock(return_value={"subscriptionId": "sub_1", "status": "active"})
    monkeypatch.setattr(server_module.client, "update_subscription_status", mock)
    await server_module.cancel_subscription(subscription_id="sub_1", at_cycle_end=True, access_token="tok")
    mock.assert_awaited_once_with("tok", "sub_1", "cancelled", at_cycle_end=True)


@pytest.mark.asyncio
async def test_process_due_subscriptions_returns_processed_results(monkeypatch):
    mock = AsyncMock(return_value={"processed": 2, "results": [{"subscriptionId": "sub_1", "billed": True}]})
    monkeypatch.setattr(server_module.client, "process_due_subscriptions", mock)
    result = await server_module.process_due_subscriptions(access_token="tok")
    assert result["success"] is True
    assert result["processed"] == 2
    mock.assert_awaited_once_with("tok")


@pytest.mark.asyncio
async def test_process_due_subscriptions_takes_no_other_arguments():
    """The tool must not accept any filtering/targeting argument the LLM
    could use to narrow or otherwise influence which subscriptions get
    billed — it is merchant-scoped only, by design, with no parameters."""
    import inspect

    sig = inspect.signature(server_module.process_due_subscriptions)
    assert set(sig.parameters) == {"access_token"}


# ---------------------------------------------------------------------------
# Analytics / Activity / Settlements / Payment Links
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_analytics_wraps_result_under_analytics_key(monkeypatch):
    monkeypatch.setattr(server_module.client, "get_analytics_summary", AsyncMock(return_value={"overview": {"totalPayments": 5}}))
    result = await server_module.get_analytics(access_token="tok")
    assert result == {"success": True, "analytics": {"overview": {"totalPayments": 5}}}


@pytest.mark.asyncio
async def test_get_activity_forwards_filters(monkeypatch):
    mock = AsyncMock(return_value={"items": [], "page": 1, "limit": 50, "total": 0})
    monkeypatch.setattr(server_module.client, "get_activity", mock)
    await server_module.get_activity(action="REFUND_CREATED", entity_type="refund", access_token="tok")
    mock.assert_awaited_once_with(
        "tok", action="REFUND_CREATED", entity_type="refund", date_from=None, date_to=None, page=None, limit=None
    )


@pytest.mark.asyncio
async def test_cancel_payment_link_calls_status_cancelled(monkeypatch):
    mock = AsyncMock(return_value={"paymentLinkId": "pl_1", "status": "cancelled"})
    monkeypatch.setattr(server_module.client, "update_payment_link_status", mock)
    await server_module.cancel_payment_link(payment_link_id="pl_1", access_token="tok")
    mock.assert_awaited_once_with("tok", "pl_1", "cancelled")


@pytest.mark.asyncio
async def test_get_settlements_summary_still_backward_compatible_with_no_filters(monkeypatch):
    mock = AsyncMock(return_value={"items": [], "summary": {"totalSettled": 0}})
    monkeypatch.setattr(server_module.client, "get_settlements", mock)
    result = await server_module.get_settlements_summary(access_token="tok")
    assert result["success"] is True
    mock.assert_awaited_once_with("tok", status=None, date_from=None, date_to=None)


# ---------------------------------------------------------------------------
# search_payments — additive filters forwarded with correct camelCase names
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_payments_forwards_new_filters(monkeypatch):
    mock = AsyncMock(return_value={"items": [], "count": 0})
    monkeypatch.setattr(server_module.client, "search_payments", mock)
    await server_module.search_payments(
        min_amount=1000, max_amount=5000, payment_method="UPI", sort_by="amount", sort_order="desc",
        page=1, limit=20, access_token="tok",
    )
    mock.assert_awaited_once_with(
        "tok",
        customerName=None,
        customerId=None,
        amount=None,
        minAmount=1000,
        maxAmount=5000,
        status=None,
        paymentMethod="UPI",
        dateFrom=None,
        dateTo=None,
        sortBy="amount",
        sortOrder="desc",
        page=1,
        limit=20,
    )
