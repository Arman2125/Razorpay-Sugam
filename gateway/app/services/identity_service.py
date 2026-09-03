"""
Resolves "who is this WhatsApp number" purely against the local
merchant_directory_entries mirror — no live Mini-Razorpay call on the hot
path, exactly mirroring Sugam AI OS's find_by_whatsapp_number() pattern for
Playground.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MerchantDirectoryEntry
from app.utils.phone import normalize_phone

logger = logging.getLogger(__name__)


async def resolve_merchant(session: AsyncSession, whatsapp_number: str) -> MerchantDirectoryEntry | None:
    normalized = normalize_phone(whatsapp_number)
    if not normalized:
        return None

    rows = (
        await session.scalars(
            select(MerchantDirectoryEntry).where(
                MerchantDirectoryEntry.phone_number_normalized == normalized,
                MerchantDirectoryEntry.status == "active",
            )
        )
    ).all()

    if len(rows) == 0:
        return None
    if len(rows) > 1:
        # Should be structurally impossible — Mini-Razorpay enforces
        # phoneNumber unique — but never guess which one if it ever happens.
        logger.error("Multiple active merchant_directory_entries for normalized number %s", normalized)
        return None
    return rows[0]
