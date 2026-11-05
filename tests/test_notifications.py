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


# ── send_message: conexión, contraseña y reintento por 465 ──────────────────
import aiosmtplib

CFG = {"host": "smtp.gmail.com", "port": 587, "username": "yo@gmail.com",
       "password": "abcd efgh ijkl mnop", "from_address": "yo@gmail.com", "use_tls": True}


@pytest.mark.asyncio
async def test_send_message_uses_starttls_and_strips_password_spaces():
    with patch("app.services.notifications.aiosmtplib.send", new_callable=AsyncMock) as send:
        from app.services.notifications import send_message
        await send_message(MagicMock(), CFG)
    kw = send.call_args.kwargs
    assert kw["port"] == 587 and kw["start_tls"] is True and kw["use_tls"] is False
    assert kw["password"] == "abcdefghijklmnop"


@pytest.mark.asyncio
async def test_send_message_retries_on_465_when_587_disconnects():
    send = AsyncMock(side_effect=[aiosmtplib.SMTPServerDisconnected("Unexpected EOF received"), None])
    with patch("app.services.notifications.aiosmtplib.send", send):
        from app.services.notifications import send_message
        await send_message(MagicMock(), CFG)
    second = send.call_args_list[1].kwargs
    assert second["port"] == 465 and second["use_tls"] is True and second["start_tls"] is False


@pytest.mark.asyncio
async def test_send_message_explains_disconnect_on_both_ports():
    send = AsyncMock(side_effect=aiosmtplib.SMTPServerDisconnected("Unexpected EOF received"))
    with patch("app.services.notifications.aiosmtplib.send", send):
        from app.services.notifications import SmtpSendError, send_message
        with pytest.raises(SmtpSendError) as err:
            await send_message(MagicMock(), CFG)
    assert "587 y 465" in str(err.value) and "red" in str(err.value)


@pytest.mark.asyncio
async def test_send_message_explains_bad_password_without_retry():
    send = AsyncMock(side_effect=aiosmtplib.SMTPAuthenticationError(535, "Username and Password not accepted"))
    with patch("app.services.notifications.aiosmtplib.send", send):
        from app.services.notifications import SmtpSendError, send_message
        with pytest.raises(SmtpSendError) as err:
            await send_message(MagicMock(), CFG)
    assert send.await_count == 1
    assert "contraseña de aplicación" in str(err.value)


@pytest.mark.asyncio
async def test_smtp_test_endpoint_sends_with_saved_password():
    with (
        patch("app.services.notifications._get_smtp_config", return_value={**CFG, "enabled": True}),
        patch("app.services.notifications.aiosmtplib.send", new_callable=AsyncMock) as send,
    ):
        from app.services.gestion import test_smtp_settings
        res = await test_smtp_settings()
    assert res == {"ok": True}
    assert send.call_args.kwargs["password"] == "abcdefghijklmnop"


@pytest.mark.asyncio
async def test_paid_email_shows_amount_and_method():
    with (
        patch("app.services.notifications._get_smtp_config", return_value={**CFG, "enabled": True}),
        patch("app.services.notifications._send_smtp", new_callable=AsyncMock) as send,
        patch("app.services.notifications._log_email"),
    ):
        from app.services.notifications import send_delivery_email
        await send_delivery_email(ORDER, "paid", {"amount": "S/ 51.68", "method": "Yape", "reference": "", "invoice": "B001-12",
                                                  "balance": "S/ 0.00", "fully_paid": True})
    kw = send.call_args.kwargs
    assert kw["subject"] == "Recibimos tu pago del pedido OV-2026-0001"
    assert "S/ 51.68" in kw["html_body"] and "Yape" in kw["html_body"] and "pagado por completo" in kw["html_body"]


@pytest.mark.asyncio
async def test_on_the_way_email_shows_driver_and_vehicle():
    with (
        patch("app.services.notifications._get_smtp_config", return_value={**CFG, "enabled": True}),
        patch("app.services.notifications._send_smtp", new_callable=AsyncMock) as send,
        patch("app.services.notifications._log_email"),
    ):
        from app.services.notifications import send_delivery_email
        await send_delivery_email(ORDER, "dispatched", {"driver_name": "Wilber Miranda", "vehicle": "F3Q-920"})
    assert "Wilber Miranda" in send.call_args.kwargs["html_body"] and "F3Q-920" in send.call_args.kwargs["html_body"]
