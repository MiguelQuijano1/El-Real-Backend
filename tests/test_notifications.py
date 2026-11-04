"""Prueba del servicio de notificaciones por email (sin SMTP real)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


ORDER = {
    "id": "uuid-order-1",
    "code": "OV-2026-0001",
    "party_name": "Acme Corp",
    "party_email": "cliente@acme.pe",
    "party_address": "Av. Larco 345",
}


@pytest.mark.asyncio
async def test_send_dispatched_email_calls_smtp():
    with (
        patch("app.services.notifications._get_smtp_config") as mock_cfg,
        patch("app.services.notifications._send_smtp", new_callable=AsyncMock) as mock_send,
        patch("app.services.notifications._log_email") as mock_log,
    ):
        mock_cfg.return_value = {
            "host": "smtp.test.com", "port": 587, "username": "u",
            "password": "p", "from_address": "no-reply@test.com",
            "use_tls": True, "enabled": True,
        }
        from app.services.notifications import send_delivery_email
        await send_delivery_email(ORDER, "dispatched")

    mock_send.assert_called_once()
    args = mock_send.call_args[1]
    assert "OV-2026-0001" in args["html_body"]
    mock_log.assert_called_once_with(ORDER["id"], "dispatched", ORDER["party_email"], None)


@pytest.mark.asyncio
async def test_no_email_when_smtp_disabled():
    with (
        patch("app.services.notifications._get_smtp_config") as mock_cfg,
        patch("app.services.notifications._send_smtp", new_callable=AsyncMock) as mock_send,
    ):
        mock_cfg.return_value = {"enabled": False}
        from app.services.notifications import send_delivery_email
        await send_delivery_email(ORDER, "delivered", extra={"receiver": "Juan"})

    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_no_email_when_no_party_email():
    order_no_email = {**ORDER, "party_email": None}
    with (
        patch("app.services.notifications._get_smtp_config") as mock_cfg,
        patch("app.services.notifications._send_smtp", new_callable=AsyncMock) as mock_send,
    ):
        mock_cfg.return_value = {"host": "smtp.test.com", "enabled": True}
        from app.services.notifications import send_delivery_email
        await send_delivery_email(order_no_email, "dispatched")

    mock_send.assert_not_called()
