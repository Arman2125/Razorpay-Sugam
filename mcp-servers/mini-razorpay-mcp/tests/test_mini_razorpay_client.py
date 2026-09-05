"""
Request-shape tests for MiniRazorpayClient's new refund/order/invoice/
subscription/analytics/activity/settlement/payment-link methods — verifies
each method builds the exact HTTP method, path, JSON body (camelCase field
names, only non-None fields included), query params, and Idempotency-Key
header Mini-Razorpay's real controllers expect (per server/src/controllers/
*.js in the mini-razorpay repo), without needing a live backend.

Also covers the shared _request()'s generic ambiguity-vs-error handling,
since no test previously existed for it at all.
"""

import json as jsonlib

import pytest

import mini_razorpay_client as client_module
from mini_razorpay_client import MiniRazorpayAPIError, MiniRazorpayClient


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = jsonlib.dumps(payload)

    def json(self):
        return self._payload


class _FakeAsyncClient:
    last_call = None
    queued_response = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, *, json=None, params=None, headers=None):
        _FakeAsyncClient.last_call = {
            "method": method,
            "url": url,
            "json": json,
            "params": params,
            "headers": headers,
        }
        return _FakeAsyncClient.queued_response


@pytest.fixture
def fake_httpx(monkeypatch):
    monkeypatch.setattr(client_module.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.last_call = None
    _FakeAsyncClient.queued_response = None
    yield _FakeAsyncClient


@pytest.fixture
def rc():
    return MiniRazorpayClient(base_url="http://testserver/api")


def _ok(data, status_code=200):
    return _FakeResponse(status_code, {"success": True, "data": data})


def _err(status_code, code, message, extra=None):
    error = {"code": code, "message": message, **(extra or {})}
    return _FakeResponse(status_code, {"success": False, "error": error})


# ---------------------------------------------------------------------------
# Shared _request() ambiguity / error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_raises_on_generic_error(fake_httpx, rc):
    fake_httpx.queued_response = _err(404, "PAYMENT_NOT_FOUND", "No such payment")
    with pytest.raises(MiniRazorpayAPIError) as exc_info:
        await rc.get_payment("tok", "pay_missing")
    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "PAYMENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_request_converts_ambiguous_customer_to_dict_not_raise(fake_httpx, rc):
    fake_httpx.queued_response = _err(
        409, "AMBIGUOUS_CUSTOMER", "Multiple customers match", {"candidates": [{"customerId": "c1"}, {"customerId": "c2"}]}
    )
    result = await rc.create_order("tok", amount=100, customer_name="Rahul")
    assert result["ambiguous"] is True
    assert result["code"] == "AMBIGUOUS_CUSTOMER"
    assert len(result["candidates"]) == 2


# ---------------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_refund_request_shape(fake_httpx, rc):
    fake_httpx.queued_response = _ok({"refundId": "rf_1", "amount": 100})
    result = await rc.create_refund("tok", payment_id="pay_1", amount=100, reason="duplicate charge", idempotency_key="idem-1")
    assert result == {"refundId": "rf_1", "amount": 100}
    call = fake_httpx.last_call
    assert call["method"] == "POST"
    assert call["url"] == "http://testserver/api/refunds"
    assert call["json"] == {"paymentId": "pay_1", "amount": 100, "reason": "duplicate charge"}
    assert call["headers"]["Authorization"] == "Bearer tok"
    assert call["headers"]["Idempotency-Key"] == "idem-1"


@pytest.mark.asyncio
async def test_create_refund_omits_none_reason(fake_httpx, rc):
    fake_httpx.queued_response = _ok({"refundId": "rf_2"})
    await rc.create_refund("tok", payment_id="pay_1", amount=50)
    assert "reason" not in fake_httpx.last_call["json"]
    assert "Idempotency-Key" not in fake_httpx.last_call["headers"]


@pytest.mark.asyncio
async def test_get_refundable_amount_path(fake_httpx, rc):
    fake_httpx.queued_response = _ok({"paymentId": "pay_1", "refundableAmount": 500})
    result = await rc.get_refundable_amount("tok", "pay_1")
    assert fake_httpx.last_call["method"] == "GET"
    assert fake_httpx.last_call["url"] == "http://testserver/api/payments/pay_1/refundable"
    assert result["refundableAmount"] == 500


@pytest.mark.asyncio
async def test_get_refunds_query_params(fake_httpx, rc):
    fake_httpx.queued_response = _ok({"items": [], "total": 0})
    await rc.get_refunds("tok", status="refunded", payment_id="pay_1", page=2, limit=10)
    assert fake_httpx.last_call["params"] == {"status": "refunded", "paymentId": "pay_1", "page": 2, "limit": 10}


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_order_request_shape(fake_httpx, rc):
    fake_httpx.queued_response = _ok({"orderId": "order_1"}, status_code=201)
    await rc.create_order(
        "tok", amount=2500, customer_id="cust_1", currency="INR", receipt="rcpt-1", notes={"foo": "bar"}
    )
    assert fake_httpx.last_call["json"] == {
        "customerId": "cust_1",
        "amount": 2500,
        "currency": "INR",
        "receipt": "rcpt-1",
        "notes": {"foo": "bar"},
    }


@pytest.mark.asyncio
async def test_update_order_status_includes_optional_payment_id(fake_httpx, rc):
    fake_httpx.queued_response = _ok({"orderId": "order_1", "status": "paid"})
    await rc.update_order_status("tok", "order_1", "paid", payment_id="pay_9")
    assert fake_httpx.last_call["method"] == "PATCH"
    assert fake_httpx.last_call["url"] == "http://testserver/api/orders/order_1/status"
    assert fake_httpx.last_call["json"] == {"status": "paid", "paymentId": "pay_9"}


@pytest.mark.asyncio
async def test_update_order_status_omits_payment_id_when_absent(fake_httpx, rc):
    fake_httpx.queued_response = _ok({"orderId": "order_1", "status": "cancelled"})
    await rc.update_order_status("tok", "order_1", "cancelled")
    assert fake_httpx.last_call["json"] == {"status": "cancelled"}


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_invoice_request_shape(fake_httpx, rc):
    fake_httpx.queued_response = _ok({"invoiceId": "inv_1"}, status_code=201)
    await rc.create_invoice("tok", amount=1000, customer_name="Rahul", due_date="2026-01-01")
    assert fake_httpx.last_call["json"] == {"customerName": "Rahul", "amount": 1000, "dueDate": "2026-01-01"}


@pytest.mark.asyncio
async def test_update_invoice_fields_only_draft_editable_fields(fake_httpx, rc):
    fake_httpx.queued_response = _ok({"invoiceId": "inv_1", "amount": 2000})
    await rc.update_invoice_fields("tok", "inv_1", amount=2000)
    assert fake_httpx.last_call["method"] == "PATCH"
    assert fake_httpx.last_call["url"] == "http://testserver/api/invoices/inv_1"
    assert fake_httpx.last_call["json"] == {"amount": 2000}


@pytest.mark.asyncio
async def test_update_invoice_status_issued(fake_httpx, rc):
    fake_httpx.queued_response = _ok({"invoiceId": "inv_1", "status": "issued"})
    await rc.update_invoice_status("tok", "inv_1", "issued")
    assert fake_httpx.last_call["url"] == "http://testserver/api/invoices/inv_1/status"
    assert fake_httpx.last_call["json"] == {"status": "issued"}


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_subscription_request_shape(fake_httpx, rc):
    fake_httpx.queued_response = _ok({"subscriptionId": "sub_1"}, status_code=201)
    await rc.create_subscription(
        "tok", amount=500, interval="month", customer_id="cust_1", interval_count=1, start_at="2026-01-01"
    )
    assert fake_httpx.last_call["json"] == {
        "customerId": "cust_1",
        "amount": 500,
        "interval": "month",
        "intervalCount": 1,
        "startAt": "2026-01-01",
    }


@pytest.mark.asyncio
async def test_update_subscription_status_cancel_at_cycle_end_true(fake_httpx, rc):
    fake_httpx.queued_response = _ok({"subscriptionId": "sub_1", "status": "active"})
    await rc.update_subscription_status("tok", "sub_1", "cancelled", at_cycle_end=True)
    assert fake_httpx.last_call["json"] == {"status": "cancelled", "atCycleEnd": True}


@pytest.mark.asyncio
async def test_update_subscription_status_at_cycle_end_false_is_kept_not_dropped(fake_httpx, rc):
    """atCycleEnd=False is a meaningful explicit choice (immediate cancel) —
    the None-filtering must not treat False as 'absent'."""
    fake_httpx.queued_response = _ok({"subscriptionId": "sub_1", "status": "cancelled"})
    await rc.update_subscription_status("tok", "sub_1", "cancelled", at_cycle_end=False)
    assert fake_httpx.last_call["json"] == {"status": "cancelled", "atCycleEnd": False}


@pytest.mark.asyncio
async def test_process_due_subscriptions_posts_empty_body(fake_httpx, rc):
    fake_httpx.queued_response = _ok({"processed": 0, "results": []})
    await rc.process_due_subscriptions("tok")
    assert fake_httpx.last_call["method"] == "POST"
    assert fake_httpx.last_call["url"] == "http://testserver/api/subscriptions/process-due"
    assert fake_httpx.last_call["json"] == {}


# ---------------------------------------------------------------------------
# Analytics / Activity / Settlements / Payment Links
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_analytics_summary_path(fake_httpx, rc):
    fake_httpx.queued_response = _ok({"overview": {}})
    await rc.get_analytics_summary("tok")
    assert fake_httpx.last_call["url"] == "http://testserver/api/analytics/summary"
    assert fake_httpx.last_call["params"] is None


@pytest.mark.asyncio
async def test_get_activity_maps_date_range_to_from_to(fake_httpx, rc):
    fake_httpx.queued_response = _ok({"items": [], "page": 1, "limit": 50, "total": 0})
    await rc.get_activity("tok", action="REFUND_CREATED", entity_type="refund", date_from="2026-01-01", date_to="2026-02-01")
    assert fake_httpx.last_call["params"] == {
        "action": "REFUND_CREATED",
        "entityType": "refund",
        "from": "2026-01-01",
        "to": "2026-02-01",
    }


@pytest.mark.asyncio
async def test_get_settlements_filters_never_break_unfiltered_call(fake_httpx, rc):
    fake_httpx.queued_response = _ok({"items": [], "summary": {}})
    await rc.get_settlements("tok")
    assert fake_httpx.last_call["params"] is None


@pytest.mark.asyncio
async def test_get_settlements_with_filters(fake_httpx, rc):
    fake_httpx.queued_response = _ok({"items": [], "summary": {}})
    await rc.get_settlements("tok", status="processed", date_from="2026-01-01")
    assert fake_httpx.last_call["params"] == {"status": "processed", "from": "2026-01-01"}


@pytest.mark.asyncio
async def test_get_settlement_by_id_path(fake_httpx, rc):
    fake_httpx.queued_response = _ok({"settlementId": "st_1"})
    await rc.get_settlement("tok", "st_1")
    assert fake_httpx.last_call["url"] == "http://testserver/api/settlements/st_1"


@pytest.mark.asyncio
async def test_update_payment_link_status_cancel(fake_httpx, rc):
    fake_httpx.queued_response = _ok({"paymentLinkId": "pl_1", "status": "cancelled"})
    await rc.update_payment_link_status("tok", "pl_1", "cancelled")
    assert fake_httpx.last_call["url"] == "http://testserver/api/payment-links/pl_1/status"
    assert fake_httpx.last_call["json"] == {"status": "cancelled"}


@pytest.mark.asyncio
async def test_get_payment_links_filters(fake_httpx, rc):
    fake_httpx.queued_response = _ok([])
    await rc.get_payment_links("tok", status="active", customer_id="cust_1")
    assert fake_httpx.last_call["params"] == {"status": "active", "customerId": "cust_1"}
