"""
Plain httpx REST client for the Mini-Razorpay API. No MCP dependency here on
purpose (mirrors Sugam AI OS's playground_client.py / server.py split) — this
module only knows how to talk HTTP to Mini-Razorpay; server.py is the only
thing that knows about MCP.

Two ambiguity codes (AMBIGUOUS_PAYMENT, AMBIGUOUS_CUSTOMER) are NOT treated as
failures here — Mini-Razorpay returning 409 with a candidates list is the
backend correctly refusing to guess, not an error. Every other >=400 response
raises MiniRazorpayAPIError.
"""

import os
from typing import Any, Optional

import httpx

BASE_URL = os.environ.get("MINI_RAZORPAY_BASE_URL", "http://localhost:5000/api").rstrip("/")

LOGIN_PATH = "/auth/login"
DEMO_MERCHANTS_PATH = "/auth/demo-merchants"
PAYMENTS_PATH = "/payments"
PAYMENTS_SEARCH_PATH = "/payments/search"
PAYMENTS_PENDING_PATH = "/payments/pending"
PAYMENTS_PENDING_PRIORITY_PATH = "/payments/pending/priority"
PAYMENTS_OVERDUE_PATH = "/payments/overdue"
PAYMENTS_SUMMARY_PATH = "/payments/summary"
REMINDERS_PATH = "/reminders"
PAYMENT_LINKS_PATH = "/payment-links"
CUSTOMERS_PATH = "/customers"
SETTLEMENTS_PATH = "/settlements"

# Ambiguity is a correct, expected outcome from these two endpoints — never an error.
_AMBIGUITY_CODES = {"AMBIGUOUS_PAYMENT", "AMBIGUOUS_CUSTOMER"}


class MiniRazorpayAPIError(Exception):
    """Raised for any Mini-Razorpay response that is a genuine failure —
    never for the two ambiguity codes above, which are returned as normal
    dicts instead (see _request())."""

    def __init__(self, status_code: int, code: str, message: str, payload: Optional[dict] = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.payload = payload or {}
        super().__init__(f"[{status_code} {code}] {message}")


class MiniRazorpayClient:
    def __init__(self, base_url: str = BASE_URL, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: Optional[str] = None,
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        url = f"{self.base_url}{path}"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.request(method, url, json=json_body, params=params, headers=headers)

        try:
            body = response.json()
        except ValueError:
            raise MiniRazorpayAPIError(response.status_code, "NON_JSON_RESPONSE", response.text[:300])

        if response.status_code >= 400:
            error = body.get("error") or {}
            code = error.get("code", "UNKNOWN_ERROR")
            message = error.get("message", "Mini-Razorpay request failed.")

            if response.status_code == 409 and code in _AMBIGUITY_CODES:
                # Not a failure — the backend correctly refused to guess.
                # Return it as a normal structured result.
                return {
                    "success": False,
                    "ambiguous": True,
                    "code": code,
                    "message": message,
                    "candidates": error.get("candidates", []),
                }

            raise MiniRazorpayAPIError(response.status_code, code, message, error)

        return body.get("data")

    # ---- Auth ----

    async def login(self, phone_number: str) -> dict:
        """Returns {"token": ..., "merchant": {...}}. phone_number must be the
        exact string Mini-Razorpay stored (e.g. "+919876543210") — login is an
        exact string match against Merchant.phoneNumber, never normalized."""
        return await self._request("POST", LOGIN_PATH, json_body={"phoneNumber": phone_number})

    async def list_demo_merchants(self) -> list[dict]:
        """Public, no auth. [{merchantId, businessName, ownerName, phoneNumber, businessType}, ...]."""
        return await self._request("GET", DEMO_MERCHANTS_PATH)

    # ---- Payments ----

    async def search_payments(self, token: str, **criteria: Any) -> dict:
        body = {k: v for k, v in criteria.items() if v is not None}
        return await self._request("POST", PAYMENTS_SEARCH_PATH, token=token, json_body=body)

    async def get_payment(self, token: str, payment_id: str) -> dict:
        return await self._request("GET", f"{PAYMENTS_PATH}/{payment_id}", token=token)

    async def get_payment_status(self, token: str, payment_id: str) -> dict:
        return await self._request("GET", f"{PAYMENTS_PATH}/{payment_id}/status", token=token)

    async def update_payment_status(self, token: str, payment_id: str, status: str) -> dict:
        return await self._request(
            "PATCH", f"{PAYMENTS_PATH}/{payment_id}/status", token=token, json_body={"status": status}
        )

    async def get_pending_payments(self, token: str) -> list[dict]:
        return await self._request("GET", PAYMENTS_PENDING_PATH, token=token)

    async def get_pending_payments_priority(self, token: str) -> list[dict]:
        return await self._request("GET", PAYMENTS_PENDING_PRIORITY_PATH, token=token)

    async def get_overdue_payments(self, token: str) -> list[dict]:
        return await self._request("GET", PAYMENTS_OVERDUE_PATH, token=token)

    async def get_payments_summary(self, token: str) -> dict:
        return await self._request("GET", PAYMENTS_SUMMARY_PATH, token=token)

    # ---- Reminders ----

    async def send_reminder(
        self,
        token: str,
        *,
        payment_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        customer_name: Optional[str] = None,
        amount: Optional[float] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        body = {
            k: v
            for k, v in {
                "paymentId": payment_id,
                "customerId": customer_id,
                "customerName": customer_name,
                "amount": amount,
            }.items()
            if v is not None
        }
        try:
            return await self._request(
                "POST", REMINDERS_PATH, token=token, json_body=body, idempotency_key=idempotency_key
            )
        except MiniRazorpayAPIError as e:
            if e.code == "DUPLICATE_REMINDER":
                return {"success": False, "duplicate": True, "code": e.code, "message": e.message}
            raise

    # ---- Payment Links ----

    async def create_payment_link(
        self,
        token: str,
        *,
        customer_id: Optional[str] = None,
        customer_name: Optional[str] = None,
        amount: Optional[float] = None,
        description: Optional[str] = None,
        expires_at: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        existing_payment_id: Optional[str] = None,
    ) -> dict:
        body = {
            k: v
            for k, v in {
                "customerId": customer_id,
                "customerName": customer_name,
                "amount": amount,
                "description": description,
                "expiresAt": expires_at,
                "existingPaymentId": existing_payment_id,
            }.items()
            if v is not None
        }
        return await self._request(
            "POST", PAYMENT_LINKS_PATH, token=token, json_body=body, idempotency_key=idempotency_key
        )

    # ---- Customers ----

    async def search_customers(self, token: str, search: str) -> list[dict]:
        return await self._request("GET", CUSTOMERS_PATH, token=token, params={"search": search})

    async def get_customer(self, token: str, customer_id: str) -> dict:
        return await self._request("GET", f"{CUSTOMERS_PATH}/{customer_id}", token=token)

    # ---- Settlements ----

    async def get_settlements(self, token: str) -> dict:
        return await self._request("GET", SETTLEMENTS_PATH, token=token)
