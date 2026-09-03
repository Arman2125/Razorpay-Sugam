"""
End-to-end scenario battery against the real running gateway (POST
/test/message), real Mini-Razorpay backend, and real Postgres. Requires
OPENAI_API_KEY (or GEMINI_API_KEY) set in gateway/.env and the gateway
running (uvicorn app.main:app --port 8100).

Uses the two real seeded demo merchants and Mini-Razorpay's own deliberate
ambiguity fixtures (Rahul Sharma's two ₹25,000 pending payments for merchant
A; the duplicate "Anita Kumar" customers for merchant A).
"""

import asyncio
import sys

import httpx

GATEWAY_URL = "http://127.0.0.1:8100"
MERCHANT_A = "+919876543210"
MERCHANT_B = "+919876543211"


async def send(client: httpx.AsyncClient, user_id: str, message: str) -> dict:
    r = await client.post(f"{GATEWAY_URL}/test/message", json={"user_id": user_id, "message": message}, timeout=30.0)
    r.raise_for_status()
    return r.json()


async def scenario(client, label, user_id, message, expect_outcome=None, expect_tool=None):
    result = await send(client, user_id, message)
    status = "OK"
    if expect_outcome and result["outcome"] != expect_outcome:
        status = f"MISMATCH (expected outcome={expect_outcome}, got {result['outcome']})"
    if expect_tool and result.get("tool_name") != expect_tool:
        status = f"MISMATCH (expected tool={expect_tool}, got {result.get('tool_name')})"
    print(f"[{status}] {label}")
    print(f"    -> outcome={result['outcome']} tool={result.get('tool_name')} reply={result['reply'][:150]!r}")
    return result


async def main():
    async with httpx.AsyncClient() as client:
        print("=== Read-only questions ===")
        await scenario(client, "pending payments", MERCHANT_A, "show me pending payments", expect_tool="get_pending_payments")
        await scenario(client, "overdue", MERCHANT_A, "what's overdue?", expect_tool="get_overdue_payments")
        await scenario(client, "summary", MERCHANT_A, "give me a payments summary", expect_tool="get_payments_summary")

        print("\n=== Ambiguous reminder (Rahul Sharma / 25000) ===")
        r1 = await scenario(
            client, "ambiguous reminder request", MERCHANT_A,
            "send a payment reminder to Rahul Sharma for 25000",
            expect_outcome="ambiguous", expect_tool="send_payment_reminder",
        )
        r2 = await scenario(client, "resolve with '1'", MERCHANT_A, "1", expect_outcome="success")
        print(f"    candidates offered: {r1['reply'][:200]}")

        print("\n=== Ambiguous payment link (Anita Kumar) ===")
        await scenario(
            client, "ambiguous customer request", MERCHANT_A,
            "create a payment link for Anita Kumar for 5000",
            expect_outcome="ambiguous", expect_tool="create_payment_link",
        )
        await scenario(client, "resolve with '2'", MERCHANT_A, "2", expect_outcome="success")

        print("\n=== Unambiguous payment link ===")
        await scenario(
            client, "unambiguous link", MERCHANT_A,
            "make a payment link for Priya Verma for 1000",
            expect_outcome="success", expect_tool="create_payment_link",
        )

        print("\n=== Cross-merchant isolation ===")
        rb = await scenario(client, "merchant B summary", MERCHANT_B, "give me a payments summary", expect_tool="get_payments_summary")
        ra = await scenario(client, "merchant A summary", MERCHANT_A, "give me a payments summary", expect_tool="get_payments_summary")
        print(f"    A and B replies differ: {ra['reply'] != rb['reply']}")

        print("\n=== Unrecognized number ===")
        await scenario(client, "unregistered number", "+911111111111", "show me pending payments", expect_outcome="declined")

        print("\n=== Off-topic ===")
        await scenario(client, "off-topic", MERCHANT_A, "what's the weather like today?", expect_outcome="no_tool")

        print("\nScenario battery complete.")


if __name__ == "__main__":
    asyncio.run(main())
