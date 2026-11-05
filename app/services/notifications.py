"""Envío de emails de seguimiento de entrega por SMTP.

Nunca lanza excepción: los errores se registran en email_notifications_log y en el log del servidor.
"""
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Literal

import aiosmtplib
from jinja2 import Environment, FileSystemLoader

from app.repositories.client import db, execute, first

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "email"
_jinja = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=True)

EventType = Literal["dispatched", "delivered", "not_delivered"]

_SUBJECTS: dict[str, str] = {
    "dispatched":    "Tu pedido {code} está en camino 🚚",
    "delivered":     "Tu pedido {code} fue entregado ✓",
    "not_delivered": "Aviso: no pudimos entregar tu pedido {code}",
}


def _get_smtp_config() -> dict[str, Any]:
    row = first(db().table("smtp_settings").select("*").eq("id", 1))
    if not row:
        return {"enabled": False}
    return row


def _log_email(order_id: str, event: str, to_email: str, error: str | None) -> None:
    try:
        execute(db().table("email_notifications_log").insert({
            "order_id": order_id, "event": event, "to_email": to_email,
            "error": error,
        }))
    except Exception as exc:
        logger.warning("No se pudo registrar email en log: %s", exc)


class SmtpSendError(Exception):
    """Fallo de envío con un mensaje en español que se puede mostrar al usuario."""


def _explain(exc: Exception, host: str, ports: list[int]) -> str:
    tried = " y ".join(str(p) for p in ports)
    if isinstance(exc, aiosmtplib.SMTPAuthenticationError):
        return ("El servidor rechazó el usuario o la contraseña. En Gmail usa una contraseña de aplicación "
                "(myaccount.google.com/apppasswords), no la de tu cuenta, y el correo completo como usuario.")
    if isinstance(exc, aiosmtplib.SMTPSenderRefused):
        return "El servidor no acepta este remitente. En Gmail el remitente debe ser el mismo correo del usuario."
    if isinstance(exc, aiosmtplib.SMTPRecipientsRefused):
        return "El servidor rechazó el correo del destinatario. Revisa que esté bien escrito."
    if isinstance(exc, aiosmtplib.SMTPServerDisconnected):
        return (f"{host} cerró la conexión antes de terminar (puerto {tried}). Suele pasar cuando la red bloquea "
                "el envío de correo (común en redes institucionales) o un antivirus revisa el correo saliente. "
                "Prueba desde otra red o desactiva el escudo de correo del antivirus.")
    if isinstance(exc, (aiosmtplib.SMTPConnectError, aiosmtplib.SMTPConnectTimeoutError, aiosmtplib.SMTPTimeoutError, OSError)):
        return (f"No se pudo conectar a {host} (puerto {tried}). Revisa el host y que la red permita la salida "
                "por ese puerto.")
    return f"Error SMTP: {exc}"


async def send_message(msg: Any, cfg: dict[str, Any]) -> None:
    """Envía `msg` con la configuración SMTP guardada. Si el puerto 587 corta la conexión, reintenta por 465 (TLS directo)."""
    host = (cfg.get("host") or "").strip()
    port = int(cfg.get("port") or 587)
    username = (cfg.get("username") or "").strip()
    # Gmail muestra la contraseña de aplicación en grupos separados por espacios.
    password = (cfg.get("password") or "").replace(" ", "")
    secure = bool(cfg.get("use_tls", True))
    auth = {"username": username, "password": password} if username else {}

    async def attempt(p: int) -> None:
        implicit = secure and p == 465
        await aiosmtplib.send(msg, hostname=host, port=p, timeout=20,
                              use_tls=implicit, start_tls=secure and not implicit, **auth)

    ports = [port]
    try:
        await attempt(port)
        return
    except (aiosmtplib.SMTPServerDisconnected, aiosmtplib.SMTPConnectError, aiosmtplib.SMTPConnectTimeoutError) as exc:
        if not secure or port == 465:
            raise SmtpSendError(_explain(exc, host, ports)) from exc
        logger.info("SMTP %s:%s falló (%s); reintentando por 465", host, port, exc)
    except Exception as exc:
        raise SmtpSendError(_explain(exc, host, ports)) from exc

    ports.append(465)
    try:
        await attempt(465)
    except Exception as exc:
        raise SmtpSendError(_explain(exc, host, ports)) from exc


async def _send_smtp(*, cfg: dict[str, Any], to_email: str, subject: str, html_body: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg.get("from_address") or cfg.get("username") or ""
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    await send_message(msg, cfg)


async def send_delivery_email(
    order: dict[str, Any],
    event: EventType,
    extra: dict[str, Any] | None = None,
) -> None:
    """Envía el email de seguimiento al cliente. No lanza excepción."""
    cfg = _get_smtp_config()
    if not cfg.get("enabled") or not cfg.get("host"):
        return
    to_email = (order.get("party_email") or "").strip()
    if not to_email:
        return

    code = order.get("code", "")
    ctx = {
        "order_code": code,
        "client_name": order.get("party_name") or "Cliente",
        "delivery_address": order.get("party_address", ""),
        "company_name": "EnterpriseCloud",
        **(extra or {}),
    }
    template = _jinja.get_template(f"{event}.html")
    html_body = template.render(**ctx)
    subject = _SUBJECTS[event].format(code=code)

    error_msg: str | None = None
    try:
        await _send_smtp(cfg=cfg, to_email=to_email, subject=subject, html_body=html_body)
        logger.info("Email '%s' enviado a %s para %s", event, to_email, code)
    except Exception as exc:
        error_msg = str(exc)
        logger.warning("Error enviando email '%s' a %s: %s", event, to_email, exc)

    _log_email(order["id"], event, to_email, error_msg)
