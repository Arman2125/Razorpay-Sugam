"""
Periodically pulls Mini-Razorpay's public GET /auth/demo-merchants into the
local merchant_directory_entries mirror — the same "sync an external app's
own directory into a local table" pattern Sugam AI OS uses for Playground,
except no credential is needed here since this endpoint is deliberately
public.
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import MerchantDirectoryEntry
from app.utils.phone import normalize_phone

logger = logging.getLogger(__name__)

DEMO_MERCHANTS_URL = f"{settings.mini_razorpay_base_url}/auth/demo-merchants"


async def sync_once(session: AsyncSession) -> int:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(DEMO_MERCHANTS_URL)
        response.raise_for_status()
        body = response.json()

    merchants = body.get("data", [])
    seen_merchant_ids: set[str] = set()

    for m in merchants:
        merchant_id = m["merchantId"]
        seen_merchant_ids.add(merchant_id)
        phone_raw = m["phoneNumber"]
        phone_normalized = normalize_phone(phone_raw)
        if not phone_normalized:
            logger.warning("Skipping merchant %s — phoneNumber %r did not normalize", merchant_id, phone_raw)
            continue

        existing = await session.scalar(
            select(MerchantDirectoryEntry).where(MerchantDirectoryEntry.merchant_id == merchant_id)
        )
        if existing:
            existing.business_name = m["businessName"]
            existing.owner_name = m.get("ownerName")
            existing.phone_number_raw = phone_raw
            existing.phone_number_normalized = phone_normalized
            existing.business_type = m.get("businessType")
            existing.status = "active"
            existing.synced_at = datetime.now(timezone.utc)
        else:
            session.add(
                MerchantDirectoryEntry(
                    merchant_id=merchant_id,
                    business_name=m["businessName"],
                    owner_name=m.get("ownerName"),
                    phone_number_raw=phone_raw,
                    phone_number_normalized=phone_normalized,
                    business_type=m.get("businessType"),
                    status="active",
                    synced_at=datetime.now(timezone.utc),
                )
            )

    # Soft-remove any merchant no longer present in the source — never delete.
    existing_rows = (await session.scalars(select(MerchantDirectoryEntry))).all()
    for row in existing_rows:
        if row.merchant_id not in seen_merchant_ids:
            row.status = "inactive"

    await session.commit()
    logger.info("Merchant directory sync complete: %d merchants seen", len(seen_merchant_ids))
    return len(seen_merchant_ids)


async def run_periodic(session_factory, interval_seconds: int) -> None:
    while True:
        try:
            async with session_factory() as session:
                await sync_once(session)
        except Exception:
            logger.exception("Merchant directory sync failed — will retry next interval")
        await asyncio.sleep(interval_seconds)
