"""Regression coverage for a real bug found during live verification against
Postgres: MerchantJwtCache.expires_at is DateTime(timezone=True), so asyncpg
always returns a timezone-AWARE datetime for it on a SELECT. get_jwt() used
to compare that against datetime.utcnow() (naive), raising
"TypeError: can't subtract offset-naive and offset-aware datetimes" on any
L1-cache-miss/L2-cache-hit — i.e. the very first request after a process
restart with an already-warm DB cache. These tests reproduce that exact
shape (a mocked row with a timezone-aware expires_at) without needing a
live database."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import jwt as pyjwt
import pytest

from app.services import merchant_auth_service


@pytest.fixture(autouse=True)
def _clear_l1_cache():
    merchant_auth_service._l1_cache.clear()
    yield
    merchant_auth_service._l1_cache.clear()


def _fake_merchant(merchant_id="M1", phone="+919876543210"):
    return MagicMock(merchant_id=merchant_id, phone_number_raw=phone)


@pytest.mark.asyncio
async def test_l2_cache_hit_with_timezone_aware_expires_at_does_not_raise(monkeypatch):
    merchant = _fake_merchant()
    row = MagicMock(
        jwt_token="cached.jwt.token",
        expires_at=datetime.now(timezone.utc) + timedelta(days=5),  # aware, as asyncpg returns it
    )
    session = MagicMock()
    session.scalar = AsyncMock(return_value=row)
    session.commit = AsyncMock()

    mock_login = AsyncMock(side_effect=AssertionError("must not re-login on a warm L2 cache hit"))
    monkeypatch.setattr(merchant_auth_service, "_login", mock_login)

    token = await merchant_auth_service.get_jwt(session, merchant)  # must not raise

    assert token == "cached.jwt.token"
    mock_login.assert_not_called()


@pytest.mark.asyncio
async def test_l1_cache_hit_skips_db_and_login_entirely():
    merchant = _fake_merchant()
    merchant_auth_service._l1_cache[merchant.merchant_id] = (
        "l1.cached.token",
        datetime.now(timezone.utc) + timedelta(days=5),
    )
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=AssertionError("must not query the DB on an L1 cache hit"))

    token = await merchant_auth_service.get_jwt(session, merchant)

    assert token == "l1.cached.token"
    session.scalar.assert_not_called()


@pytest.mark.asyncio
async def test_expired_l2_cache_triggers_a_fresh_login(monkeypatch):
    merchant = _fake_merchant()
    expired_row = MagicMock(
        jwt_token="stale.jwt.token",
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),  # aware and in the past
    )
    session = MagicMock()
    session.scalar = AsyncMock(return_value=expired_row)
    session.commit = AsyncMock()

    fresh_issued_at = datetime.now(timezone.utc)
    monkeypatch.setattr(merchant_auth_service, "_login", AsyncMock(return_value=("fresh.jwt.token", fresh_issued_at)))
    monkeypatch.setattr(merchant_auth_service, "_decode_expiry", lambda token, issued_at: issued_at + timedelta(days=7))

    token = await merchant_auth_service.get_jwt(session, merchant)

    assert token == "fresh.jwt.token"
    assert expired_row.jwt_token == "fresh.jwt.token"


def test_decode_expiry_returns_timezone_aware_datetime_from_a_real_jwt():
    issued_at = datetime.now(timezone.utc)
    exp = issued_at + timedelta(days=7)
    token = pyjwt.encode({"exp": exp}, "irrelevant-secret", algorithm="HS256")

    result = merchant_auth_service._decode_expiry(token, issued_at)

    assert result.tzinfo is not None
    assert abs((result - exp).total_seconds()) < 2


def test_decode_expiry_fallback_path_is_also_timezone_aware():
    issued_at = datetime.now(timezone.utc)

    result = merchant_auth_service._decode_expiry("not-a-valid-jwt", issued_at)

    assert result.tzinfo is not None
    assert result == issued_at + timedelta(days=7)
