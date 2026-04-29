"""Comprehensive tests for the communications layer."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auton.limbs.communications import (
    CommunicationsHub,
    NotificationDispatcher,
    VerificationCodeExtractor,
)
from auton.senses.communications import EmailClient, SmsClient


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def mock_event_bus():
    bus = AsyncMock()
    bus.emit = AsyncMock()
    return bus


@pytest.fixture
def mock_ledger():
    led = AsyncMock()
    led.charge = AsyncMock()
    return led


@pytest.fixture
def mock_tier_gate():
    return AsyncMock()


@pytest.fixture
def email_client(mock_event_bus):
    return EmailClient(
        host="test.imap.example.com",
        port=993,
        username="test@example.com",
        password="secret",
        event_bus=mock_event_bus,
    )


@pytest.fixture
def sms_client(mock_event_bus):
    return SmsClient(
        account_sid="ACtest",
        auth_token="tokentest",
        phone_number="+15551234567",
        event_bus=mock_event_bus,
    )


@pytest.fixture
def notification_dispatcher(mock_event_bus, mock_ledger):
    return NotificationDispatcher(
        email_provider="smtp",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="test@example.com",
        smtp_password="secret",
        twilio_account_sid="ACtest",
        twilio_auth_token="tokentest",
        twilio_phone_number="+15551234567",
        default_email_recipient="admin@example.com",
        default_sms_recipient="+15559876543",
        event_bus=mock_event_bus,
        ledger=mock_ledger,
    )


@pytest.fixture
def verification_extractor(mock_event_bus):
    return VerificationCodeExtractor(event_bus=mock_event_bus)


@pytest.fixture
def communications_hub(mock_event_bus, mock_ledger):
    return CommunicationsHub(
        email_provider="smtp",
        event_bus=mock_event_bus,
        ledger=mock_ledger,
    )


# --------------------------------------------------------------------------- #
# EmailClient (inbound)
# --------------------------------------------------------------------------- #


class TestEmailClient:
    @pytest.mark.asyncio
    async def test_connector_lifecycle(self, email_client):
        with patch("imaplib.IMAP4_SSL") as mock_imap_cls:
            mock_imap = MagicMock()
            mock_imap_cls.return_value = mock_imap
            await email_client.connect()
            assert email_client.connected is True
            await email_client.disconnect()
            assert email_client.connected is False

    @pytest.mark.asyncio
    async def test_is_available(self, email_client):
        assert email_client.is_available(0) is True
        assert email_client.is_available(1) is True

    @pytest.mark.asyncio
    async def test_subscription_cost(self, email_client):
        costs = email_client.get_subscription_cost()
        assert costs["monthly"] == 0.0
        assert costs["daily"] == 0.0

    @pytest.mark.asyncio
    async def test_fetch_unread(self, email_client):
        raw_email = b"""From: sender@example.com\r
To: test@example.com\r
Subject: Your verification code\r
\r
Your code is 123456.
"""
        with patch("imaplib.IMAP4_SSL") as mock_imap_cls:
            mock_imap = MagicMock()
            mock_imap_cls.return_value = mock_imap
            mock_imap.search.return_value = ("OK", [b"1"])
            mock_imap.fetch.return_value = ("OK", [(b"1", raw_email)])
            await email_client.connect()
            emails = await email_client.fetch_unread(limit=10)
            assert len(emails) == 1
            assert emails[0]["subject"] == "Your verification code"
            assert "123456" in emails[0]["body"]
            assert emails[0]["from"] == "sender@example.com"

    @pytest.mark.asyncio
    async def test_search_for_verification(self, email_client):
        raw_email = b"""From: sender@example.com\r
To: test@example.com\r
Subject: OTP Verification\r
\r
Code: 654321.
"""
        with patch("imaplib.IMAP4_SSL") as mock_imap_cls:
            mock_imap = MagicMock()
            mock_imap_cls.return_value = mock_imap
            mock_imap.search.return_value = ("OK", [b"1"])
            mock_imap.fetch.return_value = ("OK", [(b"1", raw_email)])
            await email_client.connect()
            emails = await email_client.search_for_verification(limit=10)
            assert len(emails) == 1
            assert "654321" in emails[0]["body"]

    @pytest.mark.asyncio
    async def test_fetch_data_emits_event(self, email_client, mock_event_bus):
        raw_email = b"""From: sender@example.com\r
To: test@example.com\r
Subject: Hello\r
\r
Body text.
"""
        with patch("imaplib.IMAP4_SSL") as mock_imap_cls:
            mock_imap = MagicMock()
            mock_imap_cls.return_value = mock_imap
            mock_imap.search.return_value = ("OK", [b"1"])
            mock_imap.fetch.return_value = ("OK", [(b"1", raw_email)])
            await email_client.connect()
            await email_client.fetch_data({"folder": "INBOX", "limit": 10, "search_criteria": "UNSEEN"})
            mock_event_bus.emit.assert_awaited()

    @pytest.mark.asyncio
    async def test_context_manager(self):
        client = EmailClient(
            host="test.imap.example.com",
            username="u",
            password="p",
        )
        with patch("imaplib.IMAP4_SSL") as mock_imap_cls:
            mock_imap = MagicMock()
            mock_imap_cls.return_value = mock_imap
            async with client:
                assert client.connected is True
            assert client.connected is False


# --------------------------------------------------------------------------- #
# SmsClient (inbound)
# --------------------------------------------------------------------------- #


class TestSmsClient:
    @pytest.mark.asyncio
    async def test_connector_lifecycle(self, sms_client):
        await sms_client.connect()
        assert sms_client.connected is True
        await sms_client.disconnect()
        assert sms_client.connected is False

    @pytest.mark.asyncio
    async def test_is_available(self, sms_client):
        assert sms_client.is_available(0) is True

    @pytest.mark.asyncio
    async def test_subscription_cost(self, sms_client):
        costs = sms_client.get_subscription_cost()
        assert costs["daily"] > 0.0

    @pytest.mark.asyncio
    async def test_fetch_messages(self, sms_client):
        mock_response = {
            "messages": [
                {
                    "sid": "SM123",
                    "from": "+15551111111",
                    "to": "+15551234567",
                    "body": "Your OTP is 987654",
                    "status": "received",
                    "direction": "inbound",
                    "date_sent": "2024-01-01T00:00:00Z",
                }
            ]
        }
        mock_return = MagicMock()
        mock_return.status_code = 200
        mock_return.json = MagicMock(return_value=mock_response)
        mock_return.raise_for_status = MagicMock()
        with patch.object(sms_client._client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_return
            await sms_client.connect()
            messages = await sms_client.fetch_messages(to="+15551234567", limit=1)
            assert len(messages) == 1
            assert messages[0]["sid"] == "SM123"
            assert "987654" in messages[0]["body"]

    @pytest.mark.asyncio
    async def test_fetch_data_emits_event(self, sms_client, mock_event_bus):
        mock_response = {"messages": []}
        mock_return = MagicMock()
        mock_return.status_code = 200
        mock_return.json = MagicMock(return_value=mock_response)
        mock_return.raise_for_status = MagicMock()
        with patch.object(sms_client._client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_return
            await sms_client.connect()
            await sms_client.fetch_data({"to": "+15551234567", "limit": 1})
            mock_event_bus.emit.assert_awaited()

    @pytest.mark.asyncio
    async def test_context_manager(self):
        client = SmsClient(account_sid="ACtest", auth_token="tok")
        async with client:
            assert client.connected is True
        assert client.connected is False


# --------------------------------------------------------------------------- #
# VerificationCodeExtractor
# --------------------------------------------------------------------------- #


class TestVerificationCodeExtractor:
    def test_extract_6_digit(self, verification_extractor):
        assert verification_extractor.extract_code("Your code is 123456") == "123456"

    def test_extract_code_is_format(self, verification_extractor):
        assert verification_extractor.extract_code("The code is ABC123") == "ABC123"

    def test_extract_otp_format(self, verification_extractor):
        assert verification_extractor.extract_code("OTP is 999888") == "999888"

    def test_extract_passcode_format(self, verification_extractor):
        assert verification_extractor.extract_code("Your passcode is 1122") == "1122"

    def test_extract_fallback_6_digit(self, verification_extractor):
        assert verification_extractor.extract_code("Here is 555555 for you") == "555555"

    def test_extract_no_code(self, verification_extractor):
        assert verification_extractor.extract_code("Hello, this is a normal message.") is None

    @pytest.mark.asyncio
    async def test_process_email(self, verification_extractor, mock_event_bus):
        email_data = {
            "subject": "Verify your account",
            "body": "Your verification code is 777888",
            "from": "noreply@example.com",
        }
        code = await verification_extractor.process_email(email_data)
        assert code == "777888"
        await asyncio.sleep(0.05)
        mock_event_bus.emit.assert_awaited()
        call_args = mock_event_bus.emit.await_args[0][1]
        assert call_args["code"] == "777888"
        assert call_args["source"] == "email"

    @pytest.mark.asyncio
    async def test_process_sms(self, verification_extractor, mock_event_bus):
        sms_data = {
            "body": "Code: 444333",
            "from": "+15551111111",
        }
        code = await verification_extractor.process_sms(sms_data)
        assert code == "444333"
        await asyncio.sleep(0.05)
        mock_event_bus.emit.assert_awaited()

    @pytest.mark.asyncio
    async def test_execute_extract(self, verification_extractor):
        result = await verification_extractor.execute(
            {"method": "extract", "kwargs": {"text": "OTP is 1234"}}
        )
        assert result == "1234"

    @pytest.mark.asyncio
    async def test_execute_unknown_action_raises(self, verification_extractor):
        with pytest.raises(ValueError, match="Unknown action"):
            await verification_extractor.execute({"method": "bogus"})

    @pytest.mark.asyncio
    async def test_cost_estimate(self, verification_extractor):
        cost = await verification_extractor.get_cost_estimate({"method": "extract"})
        assert cost == 0.0

    def test_is_available(self, verification_extractor):
        assert verification_extractor.is_available(0) is True

    @pytest.mark.asyncio
    async def test_health_check(self, verification_extractor):
        health = await verification_extractor.health_check()
        assert health["status"] == "healthy"


# --------------------------------------------------------------------------- #
# NotificationDispatcher
# --------------------------------------------------------------------------- #


class TestNotificationDispatcher:
    def test_is_available(self, notification_dispatcher):
        assert notification_dispatcher.is_available(0) is True

    @pytest.mark.asyncio
    async def test_cost_estimate_dispatch(self, notification_dispatcher):
        cost = await notification_dispatcher.get_cost_estimate({"method": "dispatch"})
        assert cost == pytest.approx(0.0075)

    @pytest.mark.asyncio
    async def test_cost_estimate_email(self, notification_dispatcher):
        cost = await notification_dispatcher.get_cost_estimate({"method": "send_email"})
        assert cost == pytest.approx(0.0001)

    @pytest.mark.asyncio
    async def test_dispatch_normal_priority(self, notification_dispatcher, mock_event_bus, mock_ledger):
        with patch.object(
            notification_dispatcher, "_send_via_smtp", new_callable=AsyncMock
        ) as mock_smtp:
            mock_smtp.return_value = {"status": "sent", "provider": "smtp", "to": "admin@example.com"}
            result = await notification_dispatcher.dispatch(
                alert_type="low_balance",
                message="Balance is low",
                priority="normal",
                recipients={"email": "admin@example.com"},
            )
            assert result["sent"]["email"]["status"] == "sent"
            await asyncio.sleep(0.05)
            mock_event_bus.emit.assert_awaited()
            mock_ledger.charge.assert_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_critical_priority(self, notification_dispatcher, mock_event_bus):
        with patch.object(
            notification_dispatcher, "_send_via_smtp", new_callable=AsyncMock
        ) as mock_smtp, patch.object(
            notification_dispatcher._client, "post", new_callable=AsyncMock
        ) as mock_post:
            mock_smtp.return_value = {"status": "sent", "provider": "smtp", "to": "admin@example.com"}
            mock_post.return_value.status_code = 201
            mock_post.return_value.json = MagicMock(return_value={"sid": "SM999"})
            result = await notification_dispatcher.dispatch(
                alert_type="emergency",
                message="Critical failure",
                priority="critical",
                recipients={"email": "admin@example.com", "sms": "+15559876543"},
            )
            assert "sms" in result["sent"]
            assert "email" in result["sent"]

    @pytest.mark.asyncio
    async def test_dispatch_low_priority_logs(self, notification_dispatcher):
        result = await notification_dispatcher.dispatch(
            alert_type="heartbeat",
            message="All systems nominal",
            priority="low",
        )
        assert result["sent"]["logged"] is True

    @pytest.mark.asyncio
    async def test_send_email_smtp(self, notification_dispatcher):
        with patch.object(
            notification_dispatcher, "_send_via_smtp", new_callable=AsyncMock
        ) as mock_smtp:
            mock_smtp.return_value = {"status": "sent", "provider": "smtp", "to": "a@b.com"}
            result = await notification_dispatcher.send_email("a@b.com", "Subject", "Body")
            assert result["status"] == "sent"

    @pytest.mark.asyncio
    async def test_send_email_sendgrid(self, notification_dispatcher):
        notification_dispatcher._sendgrid_api_key = "SG.test"
        with patch.object(
            notification_dispatcher._client, "post", new_callable=AsyncMock
        ) as mock_post:
            mock_post.return_value.status_code = 202
            result = await notification_dispatcher.send_email(
                "a@b.com", "Subject", "Body", provider="sendgrid"
            )
            assert result["status"] == "sent"
            assert result["provider"] == "sendgrid"

    @pytest.mark.asyncio
    async def test_send_email_unsupported_provider(self, notification_dispatcher):
        with pytest.raises(ValueError, match="Unsupported email provider"):
            await notification_dispatcher.send_email("a@b.com", "Subject", "Body", provider="unknown")

    @pytest.mark.asyncio
    async def test_send_sms(self, notification_dispatcher):
        with patch.object(
            notification_dispatcher._client, "post", new_callable=AsyncMock
        ) as mock_post:
            mock_post.return_value.status_code = 201
            mock_post.return_value.json = MagicMock(return_value={"sid": "SM123"})
            result = await notification_dispatcher.send_sms("+15559876543", "Hello")
            assert result["status"] == "sent"
            assert result["sid"] == "SM123"

    @pytest.mark.asyncio
    async def test_send_sms_not_configured(self, notification_dispatcher):
        notification_dispatcher._twilio_account_sid = ""
        result = await notification_dispatcher.send_sms("+15559876543", "Hello")
        assert result["status"] == "not_configured"

    @pytest.mark.asyncio
    async def test_execute_dispatch(self, notification_dispatcher):
        with patch.object(
            notification_dispatcher, "dispatch", new_callable=AsyncMock
        ) as mock_dispatch:
            mock_dispatch.return_value = {"sent": {}}
            await notification_dispatcher.execute(
                {
                    "method": "dispatch",
                    "kwargs": {
                        "alert_type": "test",
                        "message": "msg",
                        "priority": "normal",
                    },
                }
            )
            mock_dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_unknown_action_raises(self, notification_dispatcher):
        with pytest.raises(ValueError, match="Unknown action"):
            await notification_dispatcher.execute({"method": "bogus"})

    @pytest.mark.asyncio
    async def test_context_manager(self):
        dispatcher = NotificationDispatcher()
        async with dispatcher:
            pass


# --------------------------------------------------------------------------- #
# CommunicationsHub
# --------------------------------------------------------------------------- #


class TestCommunicationsHub:
    def test_is_available(self, communications_hub):
        assert communications_hub.is_available(0) is True

    @pytest.mark.asyncio
    async def test_health_check(self, communications_hub):
        health = await communications_hub.health_check()
        assert "status" in health
        assert "dispatcher" in health
        assert "extractor" in health

    @pytest.mark.asyncio
    async def test_send_alert(self, communications_hub, mock_event_bus):
        with patch.object(
            communications_hub._dispatcher, "dispatch", new_callable=AsyncMock
        ) as mock_dispatch:
            mock_dispatch.return_value = {"sent": {"email": {"status": "sent"}}}
            result = await communications_hub.send_alert(
                "opportunity", "Found a trade!", priority="normal"
            )
            assert result["sent"]["email"]["status"] == "sent"
            mock_dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_extract_from_email(self, communications_hub):
        email_data = {
            "subject": "Verify",
            "body": "Code is 112233",
            "from": "test@example.com",
        }
        code = await communications_hub.extract_from_email(email_data)
        assert code == "112233"

    @pytest.mark.asyncio
    async def test_extract_from_sms(self, communications_hub):
        sms_data = {"body": "OTP is 445566", "from": "+15551111111"}
        code = await communications_hub.extract_from_sms(sms_data)
        assert code == "445566"

    @pytest.mark.asyncio
    async def test_execute_notifications(self, communications_hub):
        with patch.object(
            communications_hub._dispatcher, "execute", new_callable=AsyncMock
        ) as mock_exec:
            mock_exec.return_value = {"status": "sent"}
            await communications_hub.execute(
                {
                    "subsystem": "notifications",
                    "method": "send_email",
                    "kwargs": {"to": "a@b.com", "subject": "S", "body": "B"},
                }
            )
            mock_exec.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_extractor(self, communications_hub):
        with patch.object(
            communications_hub._extractor, "execute", new_callable=AsyncMock
        ) as mock_exec:
            mock_exec.return_value = "999000"
            await communications_hub.execute(
                {
                    "subsystem": "extractor",
                    "method": "extract",
                    "kwargs": {"text": "Code is 999000"},
                }
            )
            mock_exec.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_unknown_subsystem_raises(self, communications_hub):
        with pytest.raises(ValueError, match="Unknown subsystem"):
            await communications_hub.execute(
                {"subsystem": "bogus", "method": "do", "kwargs": {}}
            )

    @pytest.mark.asyncio
    async def test_cost_estimate(self, communications_hub):
        cost = await communications_hub.get_cost_estimate(
            {"subsystem": "notifications", "method": "dispatch"}
        )
        assert cost >= 0.0

    @pytest.mark.asyncio
    async def test_context_manager(self):
        hub = CommunicationsHub()
        async with hub:
            pass
