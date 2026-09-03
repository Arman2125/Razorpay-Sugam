"""Confirms Mini-Razorpay, Postgres, and the razorpay_sugam database are all
reachable before doing anything else. Run with the gateway venv's python."""

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gateway"))

from app.config import settings  # noqa: E402
from app.db import engine  # noqa: E402


async def check_mini_razorpay() -> bool:
    health_url = settings.mini_razorpay_base_url.replace("/api", "/health")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(health_url)
        ok = r.status_code == 200
        print(f"[{'OK' if ok else 'FAIL'}] Mini-Razorpay reachable at {health_url}")
        return ok
    except Exception as e:
        print(f"[FAIL] Mini-Razorpay unreachable at {health_url}: {e}")
        return False


async def check_postgres() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        print(f"[OK] Postgres reachable, database configured correctly")
        return True
    except Exception as e:
        print(f"[FAIL] Postgres/database unreachable: {e}")
        return False


async def main():
    results = await asyncio.gather(check_mini_razorpay(), check_postgres())
    if all(results):
        print("\nAll prerequisites satisfied.")
        sys.exit(0)
    else:
        print("\nOne or more prerequisites failed — fix before proceeding.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
