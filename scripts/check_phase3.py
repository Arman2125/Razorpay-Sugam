"""Scratch verification for Phase 3: directory sync, identity resolution,
and per-merchant JWT caching — run against the real Mini-Razorpay backend
and real Postgres. Not a permanent test file; safe to delete/rewrite later."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gateway"))

from app.db import AsyncSessionLocal  # noqa: E402
from app.services import directory_sync_service, identity_service, merchant_auth_service  # noqa: E402


async def main():
    async with AsyncSessionLocal() as session:
        count = await directory_sync_service.sync_once(session)
        print(f"Synced {count} merchants")

    async with AsyncSessionLocal() as session:
        for variant in ["+919876543210", "919876543210", "9876543210", "09876543210"]:
            merchant = await identity_service.resolve_merchant(session, variant)
            print(f"  {variant!r} -> {merchant.business_name if merchant else None}")

        unknown = await identity_service.resolve_merchant(session, "+919999999999")
        print(f"  unregistered number -> {unknown}")

    async with AsyncSessionLocal() as session:
        merchant = await identity_service.resolve_merchant(session, "+919876543210")
        token1 = await merchant_auth_service.get_jwt(session, merchant)
        print(f"First get_jwt call: token len={len(token1)}")

    async with AsyncSessionLocal() as session:
        merchant = await identity_service.resolve_merchant(session, "+919876543210")
        token2 = await merchant_auth_service.get_jwt(session, merchant)
        print(f"Second get_jwt call: token len={len(token2)}, same token: {token1 == token2}")

    print("Phase 3 checks complete.")


if __name__ == "__main__":
    asyncio.run(main())
