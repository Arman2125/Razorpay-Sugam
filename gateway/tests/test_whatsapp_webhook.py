"""Reproduces, at the route level, the production bug of a merchant
receiving the same reply multiple times: Meta's Cloud API redelivers the
same inbound message (same message_id) more than once, and prior to the
dedup guard the webhook handler re-ran the full pipeline (LLM + MCP +
outbound send) for every delivery. This proves the fix: a second POST with
the same message_id must still return 200 (Meta must never see a failure)
but must never call process_user_message or send_text_message again."""

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.routes import whatsapp_webhook

SECRET = "test_app_secret"


class _FakeSessionCtx:
    async def __aenter__(self):
        return MagicMock()

    async def __aexit__(self, *exc):
        return False


def _signed(body: dict) -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}


def _message_payload(message_id="wamid.abc123"):
    return {
        "entry": [
            {"changes": [{"value": {"messages": [{"id": message_id, "from": "+919876543210", "text": {"body": "hi"}}]}}]}
        ]
    }


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", SECRET)
    monkeypatch.setattr(whatsapp_webhook, "AsyncSessionLocal", lambda: _FakeSessionCtx())
    monkeypatch.setattr(whatsapp_webhook, "mark_message_as_read", AsyncMock())
    monkeypatch.setattr(whatsapp_webhook, "process_user_message", AsyncMock(return_value=MagicMock(reply="hello back")))
    monkeypatch.setattr(whatsapp_webhook, "send_text_message", AsyncMock())

    app = FastAPI()
    app.include_router(whatsapp_webhook.router)
    return TestClient(app)


def test_redelivered_message_id_is_processed_and_sent_only_once(client, monkeypatch):
    seen_ids = set()

    async def fake_mark_seen(session, message_id):
        if message_id in seen_ids:
            return False
        seen_ids.add(message_id)
        return True

    monkeypatch.setattr(whatsapp_webhook.whatsapp_dedup_service, "mark_seen", fake_mark_seen)

    raw, headers = _signed(_message_payload("wamid.abc123"))

    first = client.post("/webhook/whatsapp", content=raw, headers=headers)
    second = client.post("/webhook/whatsapp", content=raw, headers=headers)  # Meta redelivery of the same event
    third = client.post("/webhook/whatsapp", content=raw, headers=headers)  # and again

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 200
    whatsapp_webhook.process_user_message.assert_awaited_once()
    whatsapp_webhook.send_text_message.assert_awaited_once()
    whatsapp_webhook.mark_message_as_read.assert_awaited_once()


def test_two_distinct_message_ids_are_both_processed(client, monkeypatch):
    seen_ids = set()

    async def fake_mark_seen(session, message_id):
        if message_id in seen_ids:
            return False
        seen_ids.add(message_id)
        return True

    monkeypatch.setattr(whatsapp_webhook.whatsapp_dedup_service, "mark_seen", fake_mark_seen)

    raw1, headers1 = _signed(_message_payload("wamid.one"))
    raw2, headers2 = _signed(_message_payload("wamid.two"))

    client.post("/webhook/whatsapp", content=raw1, headers=headers1)
    client.post("/webhook/whatsapp", content=raw2, headers=headers2)

    assert whatsapp_webhook.process_user_message.await_count == 2
    assert whatsapp_webhook.send_text_message.await_count == 2
