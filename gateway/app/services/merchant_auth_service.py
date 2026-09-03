"""
Per-merchant JWT acquisition/caching — the one piece of this integration with
no direct Sugam AI OS precedent. Playground uses a single shared service
credential for every caller; Mini-Razorpay has no such credential by design —
each merchant's own JWT is the entire cross-merchant data-isolation boundary,
so a fresh token is fetched per resolved merchant, not shared.

Two-tier cache: an in-process dict (L1, fast, per-worker) backed by the
merchant_jwt_cache table (L2, shared across worker processes, avoids
redundant logins) — logins are cheap and infrequent (7-day expiry) so the
DB round trip on an L1 miss costs nothing.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx
import jwt as pyjwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import MerchantDirectoryEntry, MerchantJwtCache

logger = logging.getLogger(__name__)

LOGIN_URL = f"{settings.mini_razorpay_base_url}/auth/login"

# merchant_id -> (token, expires_at)
_l1_cache: dict[str, tuple[str, datetime]] = {}


class MerchantAuthError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _decode_expiry(token: str, issued_at: datetime) -> datetime:
    """Reads the JWT's own exp claim without verifying its signature — this
    gateway doesn't hold, and shouldn't hold, Mini-Razorpay's JWT_SECRET; it
    only ever reads its own freshly-issued token's expiry, never an
    untrusted token from anywhere else. Returns a timezone-aware UTC
    datetime — merchant_jwt_cache.expires_at is DateTime(timezone=True), and
    asyncpg always hands back an aware datetime for that column on a later
    SELECT, so this must be aware too or the comparisons below raise
    TypeError: can't subtract offset-naive and offset-aware datetimes."""
    try:
        payload = pyjwt.decode(token, options={"verify_signature": False})
        exp = payload.get("exp")
        if exp:
            return datetime.fromtimestamp(exp, tz=timezone.utc)
    except Exception:
        logger.warning("Could not decode JWT exp claim; falling back to a 7-day default expiry")
    return issued_at + timedelta(days=7)


async def _login(phone_number_raw: str) -> tuple[str, datetime]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(LOGIN_URL, json={"phoneNumber": phone_number_raw})

    body = response.json()
    if response.status_code >= 400 or not body.get("success"):
        error = body.get("error", {})
        raise MerchantAuthError(error.get("message", "Login to Mini-Razorpay failed."))

    token = body["data"]["token"]
    issued_at = datetime.now(timezone.utc)
    return token, issued_at


async def get_jwt(session: AsyncSession, merchant: MerchantDirectoryEntry) -> str:
    now = datetime.now(timezone.utc)
    safety_margin = timedelta(seconds=settings.jwt_cache_safety_margin_seconds)

    # L1: in-process cache
    cached = _l1_cache.get(merchant.merchant_id)
    if cached and cached[1] - now > safety_margin:
        return cached[0]

    # L2: DB-backed cache
    row = await session.scalar(select(MerchantJwtCache).where(MerchantJwtCache.merchant_id == merchant.merchant_id))
    if row and row.expires_at - now > safety_margin:
        _l1_cache[merchant.merchant_id] = (row.jwt_token, row.expires_at)
        row.last_used_at = now
        await session.commit()
        return row.jwt_token

    # Miss — real login, using the exact stored phone number string.
    try:
        token, issued_at = await _login(merchant.phone_number_raw)
    except MerchantAuthError:
        raise
    except Exception as e:
        logger.exception("Unexpected error logging in to Mini-Razorpay for merchant %s", merchant.merchant_id)
        raise MerchantAuthError("Could not reach Mini-Razorpay to authenticate this merchant.") from e

    expires_at = _decode_expiry(token, issued_at)

    if row:
        row.jwt_token = token
        row.issued_at = issued_at
        row.expires_at = expires_at
        row.last_used_at = now
    else:
        session.add(
            MerchantJwtCache(
                merchant_id=merchant.merchant_id,
                jwt_token=token,
                issued_at=issued_at,
                expires_at=expires_at,
                last_used_at=now,
            )
        )
    await session.commit()

    _l1_cache[merchant.merchant_id] = (token, expires_at)
    return token
