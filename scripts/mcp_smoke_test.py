"""
Standalone smoke test for the mini-razorpay-mcp server — connects a real MCP
stdio client session to server.py (no gateway involved) and calls every tool
against the real, running Mini-Razorpay backend. Run with the mcp-servers
venv's python:

    mcp-servers/mini-razorpay-mcp/.venv/Scripts/python.exe scripts/mcp_smoke_test.py <token_A> <token_B>

Where token_A/token_B are real JWTs for the two demo merchants (get them via
POST /api/auth/login {"phoneNumber": "+919876543210"} / "...211").
"""

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_PATH = str(Path(__file__).resolve().parent.parent / "mcp-servers" / "mini-razorpay-mcp" / "server.py")


def pretty(label, result):
    content = result.content[0].text if result.content else "<empty>"
    print(f"\n=== {label} ===")
    try:
        print(json.dumps(json.loads(content), indent=2, ensure_ascii=False)[:1500])
    except Exception:
        print(content[:1500])


async def main():
    token_a = sys.argv[1]
    token_b = sys.argv[2] if len(sys.argv) > 2 else None

    params = StdioServerParameters(command=sys.executable, args=[SERVER_PATH])

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print(f"Discovered {len(tools.tools)} tools:", [t.name for t in tools.tools])

            r = await session.call_tool("get_payments_summary", {"access_token": token_a})
            pretty("get_payments_summary (merchant A)", r)

            r = await session.call_tool("get_pending_payments", {"access_token": token_a})
            pretty("get_pending_payments (merchant A)", r)

            r = await session.call_tool(
                "search_payments",
                {"customer_name": "Rahul Sharma", "amount": 25000, "access_token": token_a},
            )
            pretty("search_payments Rahul Sharma 25000 (merchant A) — expect 2 items", r)

            r = await session.call_tool(
                "send_payment_reminder",
                {"customer_name": "Rahul Sharma", "amount": 25000, "access_token": token_a},
            )
            pretty("send_payment_reminder ambiguous (merchant A) — expect ambiguous:true, 2 candidates", r)

            r = await session.call_tool(
                "create_payment_link",
                {"customer_name": "Anita Kumar", "amount": 5000, "access_token": token_a},
            )
            pretty("create_payment_link ambiguous customer (merchant A) — expect ambiguous:true, 2 candidates", r)

            r = await session.call_tool(
                "create_payment_link",
                {"customer_name": "Priya Verma", "amount": 1234, "access_token": token_a},
            )
            pretty("create_payment_link unambiguous (merchant A) — expect real paymentLinkId + shortUrl", r)

            r = await session.call_tool("search_customers", {"search": "rahul", "access_token": token_a})
            pretty("search_customers 'rahul' (merchant A)", r)

            r = await session.call_tool("get_settlements_summary", {"access_token": token_a})
            pretty("get_settlements_summary (merchant A)", r)

            r = await session.call_tool("get_pending_payments_priority", {"access_token": token_a})
            pretty("get_pending_payments_priority (merchant A)", r)

            r = await session.call_tool("get_overdue_payments", {"access_token": token_a})
            pretty("get_overdue_payments (merchant A)", r)

            if token_b:
                r = await session.call_tool("get_payments_summary", {"access_token": token_b})
                pretty("get_payments_summary (merchant B) — should differ from A", r)

            # no token at all
            r = await session.call_tool("get_payments_summary", {})
            pretty("get_payments_summary NO TOKEN — expect MISSING_TOKEN error", r)

            print("\nSmoke test complete.")


if __name__ == "__main__":
    asyncio.run(main())
