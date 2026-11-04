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


async def _send_smtp(*, host: str, port: int, username: str, password: str,
                     from_address: str, use_tls: bool,
                     to_email: str, subject: str, html_body: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_address
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    await aiosmtplib.send(
        msg,
        hostname=host, port=port,
        username=username, password=password,
        start_tls=use_tls,
    )


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
        await _send_smtp(
            host=cfg["host"], port=int(cfg.get("port", 587)),
            username=cfg.get("username", ""), password=cfg.get("password", ""),
            from_address=cfg.get("from_address", cfg["host"]),
            use_tls=bool(cfg.get("use_tls", True)),
            to_email=to_email, subject=subject, html_body=html_body,
        )
        logger.info("Email '%s' enviado a %s para %s", event, to_email, code)
    except Exception as exc:
        error_msg = str(exc)
        logger.warning("Error enviando email '%s' a %s: %s", event, to_email, exc)

    _log_email(order["id"], event, to_email, error_msg)
