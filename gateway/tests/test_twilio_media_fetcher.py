"""Twilio media download — mocked httpx transport, no live Twilio account
or network access required."""

import httpx
import pytest

from app.twilio import media_fetcher


@pytest.mark.asyncio
async def test_successful_fetch_returns_bytes(monkeypatch):
    monkeypatch.setattr(media_fetcher.settings, "twilio_account_sid", "AC_test")
    monkeypatch.setattr(media_fetcher.settings, "twilio_auth_token", "test_token")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(200, content=b"audio-bytes-here")

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(media_fetcher.httpx, "AsyncClient", fake_client)

    result = await media_fetcher.fetch_media("https://api.twilio.com/fake-media-url")
    assert result == b"audio-bytes-here"


@pytest.mark.asyncio
async def test_http_error_status_returns_none(monkeypatch):
    monkeypatch.setattr(media_fetcher.settings, "twilio_account_sid", "AC_test")
    monkeypatch.setattr(media_fetcher.settings, "twilio_auth_token", "test_token")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(media_fetcher.httpx, "AsyncClient", fake_client)

    result = await media_fetcher.fetch_media("https://api.twilio.com/missing-media")
    assert result is None


@pytest.mark.asyncio
async def test_network_exception_returns_none(monkeypatch):
    monkeypatch.setattr(media_fetcher.settings, "twilio_account_sid", "AC_test")
    monkeypatch.setattr(media_fetcher.settings, "twilio_auth_token", "test_token")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(media_fetcher.httpx, "AsyncClient", fake_client)

    result = await media_fetcher.fetch_media("https://api.twilio.com/unreachable")
    assert result is None


@pytest.mark.asyncio
async def test_empty_url_returns_none_without_making_a_request():
    result = await media_fetcher.fetch_media("")
    assert result is None


@pytest.mark.asyncio
async def test_oversized_media_returns_none(monkeypatch):
    monkeypatch.setattr(media_fetcher.settings, "twilio_account_sid", "AC_test")
    monkeypatch.setattr(media_fetcher.settings, "twilio_auth_token", "test_token")
    monkeypatch.setattr(media_fetcher, "_MAX_MEDIA_BYTES", 10)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"this payload is way over ten bytes")

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(media_fetcher.httpx, "AsyncClient", fake_client)

    result = await media_fetcher.fetch_media("https://api.twilio.com/big-media")
    assert result is None
