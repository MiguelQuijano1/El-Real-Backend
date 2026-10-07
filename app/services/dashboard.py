from typing import Any

from app.repositories.client import db, execute, rows


def summary(period: str) -> dict[str, Any]:
    """Resumen del Dashboard: la base lo calcula (función SQL dashboard_summary) y aquí se agregan las alertas de usuarios."""
    data = execute(db().rpc("dashboard_summary", {"p_period": period})).data
    if isinstance(data, list):  # según la versión de postgrest puede venir envuelto
        data = data[0] if data else {}
    data = dict(data or {})

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