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

# Mini-Razorpay's demo-merchants endpoint has been observed returning a
# transient 429 on some requests while a direct retry moments later
# succeeds (confirmed 200 with real data) — i.e. it isn't down, just
# occasionally rate-limiting. A small, bounded retry here absorbs that
# without leaving the local merchant directory empty for a full 300-second
# periodic-loop cycle. Deliberately small and bounded: this runs
# synchronously on startup (main.py's lifespan) before the app accepts
# traffic, so it must never block for long.
_MAX_SYNC_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 8.0
_MAX_RETRY_AFTER_SECONDS = 15.0


def _parse_retry_after_seconds(response: httpx.Response) -> float | None:
    """Returns a bounded, non-negative delay from the response's
    Retry-After header, or None if it's absent or not a plain
    delta-seconds integer/float (the HTTP-date form is not handled — no
    rate limiter this gateway talks to has ever sent one)."""
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(seconds, _MAX_RETRY_AFTER_SECONDS)


def _backoff_delay_seconds(attempt: int) -> float:
    """Bounded exponential backoff used only when the 429 response gave no
    Retry-After: 1s, 2s, 4s, ... capped at _MAX_BACKOFF_SECONDS."""
    return min(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), _MAX_BACKOFF_SECONDS)


async def _fetch_demo_merchants() -> dict:
    """GETs Mini-Razorpay's demo-merchants endpoint, retrying a transient
    HTTP 429 up to _MAX_SYNC_ATTEMPTS times (honoring the response's own
    Retry-After when present and reasonable, else a bounded exponential
    backoff). Any other error status is raised immediately, exactly as
    before — this only changes behavior for a 429. If every attempt is
    429'd, the final response's raise_for_status() raises the same
    httpx.HTTPStatusError sync_once always raised on failure, so callers
    (sync_once's caller in main.py's lifespan, and run_periodic) keep
    treating a fully-exhausted retry exactly like the old single-shot
    failure — logged and non-fatal."""
    response: httpx.Response | None = None
    for attempt in range(1, _MAX_SYNC_ATTEMPTS + 1):
        logger.info(
            "Merchant directory sync attempt %d/%d: GET %s", attempt, _MAX_SYNC_ATTEMPTS, DEMO_MERCHANTS_URL
        )
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(DEMO_MERCHANTS_URL)

        if response.status_code != 429:
            response.raise_for_status()
            if attempt > 1:
                logger.info(
                    "Merchant directory sync succeeded on attempt %d/%d after retrying", attempt, _MAX_SYNC_ATTEMPTS
                )
            return response.json()

        if attempt == _MAX_SYNC_ATTEMPTS:
            logger.warning(
                "Merchant directory sync: HTTP 429 on final attempt %d/%d — giving up for this cycle",
                attempt, _MAX_SYNC_ATTEMPTS,
            )
            break

        retry_after = _parse_retry_after_seconds(response)
        if retry_after is not None:
            delay, source = retry_after, "Retry-After header"
        else:
            delay, source = _backoff_delay_seconds(attempt), "backoff"

        logger.warning(
            "Merchant directory sync: HTTP 429 (attempt %d/%d) — retrying in %.1fs (%s)",
            attempt, _MAX_SYNC_ATTEMPTS, delay, source,
        )
        await asyncio.sleep(delay)

    # Every attempt was a 429 — raise the same exception type/shape a
    # single-shot 429 always raised, so nothing downstream needs to change.
    response.raise_for_status()


async def sync_once(session: AsyncSession) -> int:
    body = await _fetch_demo_merchants()

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
    # The caller (main.py's lifespan) already performs one synchronous sync
    # before this task starts, so the loop sleeps first — otherwise every
    # startup fires two syncs back-to-back (that duplicate burst is what
    # tripped Mini-Razorpay's 429 on deploy).
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            async with session_factory() as session:
                await sync_once(session)
        except Exception:
            logger.exception("Merchant directory sync failed — will retry next interval")
