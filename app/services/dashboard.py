import logging
from typing import Any

from app.repositories.client import db, execute, rows

logger = logging.getLogger(__name__)


def summary(period: str) -> dict[str, Any]:
    """Resumen del Dashboard: la base lo calcula (función SQL dashboard_summary) y aquí se agregan las alertas de usuarios."""
    data = execute(db().rpc("dashboard_summary", {"p_period": period})).data
    if isinstance(data, list):  # según la versión de postgrest puede venir envuelto
        data = data[0] if data else {}
    data = dict(data or {})

    # Gráficos (ventas diarias y ventas por categoría): función SQL dashboard_charts (database/009_dashboard_graficos.sql).
    # Si aún no se creó en la base, el resto del Dashboard sigue funcionando y los gráficos salen vacíos.
    charts: dict[str, Any] = {}
    try:
        raw = execute(db().rpc("dashboard_charts", {"p_period": period})).data
        if isinstance(raw, list):
            raw = raw[0] if raw else {}
        charts = dict(raw or {})
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo calcular dashboard_charts")
    data["dailySales"] = charts.get("dailySales", [])
    data["salesByCategory"] = charts.get("salesByCategory", [])
    data["chartsRange"] = {"from": charts.get("from"), "to": charts.get("to")}

    pending = rows(
        db().table("users")
        .select("id, full_name, email, status, role:roles(name)")
        .in_("status", ["INVITED", "LOCKED"])
        .order("full_name")
        .limit(20)
    )
    data["userAlerts"] = [
        {"id": u["id"], "fullName": u["full_name"], "email": u["email"], "status": u["status"], "roleName": (u.get("role") or {}).get("name")}
        for u in pending
    ]
    return data