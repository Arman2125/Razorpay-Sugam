"""Outbound Twilio sender — mocked at the twilio.rest.Client boundary, no
live credentials or network access required."""

from unittest.mock import MagicMock, patch

import pytest

from app.twilio import client as twilio_client


@pytest.mark.asyncio
async def test_disabled_short_circuits_without_calling_twilio(monkeypatch):
    monkeypatch.setattr(twilio_client.settings, "twilio_enabled_raw", "false")

    with patch("app.twilio.client.Client") as mock_client_cls:
        await twilio_client.send_text_message("+919876543210", "hello")

    mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_enabled_but_missing_credentials_does_not_call_twilio(monkeypatch):
    monkeypatch.setattr(twilio_client.settings, "twilio_enabled_raw", "true")
    monkeypatch.setattr(twilio_client.settings, "twilio_account_sid", "")
    monkeypatch.setattr(twilio_client.settings, "twilio_auth_token", "")
    monkeypatch.setattr(twilio_client.settings, "twilio_whatsapp_number", "")

    with patch("app.twilio.client.Client") as mock_client_cls:
        await twilio_client.send_text_message("+919876543210", "hello")

    mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_enabled_sends_with_correct_recipient_sender_and_body(monkeypatch):
    monkeypatch.setattr(twilio_client.settings, "twilio_enabled_raw", "true")
    monkeypatch.setattr(twilio_client.settings, "twilio_account_sid", "AC_test")
    monkeypatch.setattr(twilio_client.settings, "twilio_auth_token", "test_token")
    monkeypatch.setattr(twilio_client.settings, "twilio_whatsapp_number", "+14155238886")

    mock_instance = MagicMock()
    with patch("app.twilio.client.Client", return_value=mock_instance) as mock_client_cls:
        await twilio_client.send_text_message("+919876543210", "your reminder is here")

    mock_client_cls.assert_called_once_with("AC_test", "test_token")
    mock_instance.messages.create.assert_called_once_with(
        from_="whatsapp:+14155238886",
        to="whatsapp:+919876543210",
        body="your reminder is here",
    )


@pytest.mark.asyncio
async def test_to_number_already_prefixed_is_not_double_prefixed(monkeypatch):
    monkeypatch.setattr(twilio_client.settings, "twilio_enabled_raw", "true")
    monkeypatch.setattr(twilio_client.settings, "twilio_account_sid", "AC_test")
    monkeypatch.setattr(twilio_client.settings, "twilio_auth_token", "test_token")
    monkeypatch.setattr(twilio_client.settings, "twilio_whatsapp_number", "whatsapp:+14155238886")

    mock_instance = MagicMock()
    with patch("app.twilio.client.Client", return_value=mock_instance):
        await twilio_client.send_text_message("whatsapp:+919876543210", "hi")

    mock_instance.messages.create.assert_called_once_with(
        from_="whatsapp:+14155238886",
        to="whatsapp:+919876543210",
        body="hi",
    )


@pytest.mark.asyncio
async def test_twilio_api_failure_is_caught_and_does_not_raise(monkeypatch):
    from twilio.base.exceptions import TwilioRestException

    monkeypatch.setattr(twilio_client.settings, "twilio_enabled_raw", "true")
    monkeypatch.setattr(twilio_client.settings, "twilio_account_sid", "AC_test")
    monkeypatch.setattr(twilio_client.settings, "twilio_auth_token", "test_token")
    monkeypatch.setattr(twilio_client.settings, "twilio_whatsapp_number", "+14155238886")

    mock_instance = MagicMock()
    mock_instance.messages.create.side_effect = TwilioRestException(
        status=400, uri="/Messages", msg="bad request", code=21211
    )
    with patch("app.twilio.client.Client", return_value=mock_instance):
        await twilio_client.send_text_message("+919876543210", "hi")  # must not raise
