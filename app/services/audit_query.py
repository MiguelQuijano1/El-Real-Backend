from datetime import datetime
from typing import Any

from app.repositories.client import db, execute

AUDIT_SELECT = (
    "id, occurred_at, user_id, role_id, user_name_snapshot, role_name_snapshot, action, module_key, entity_type, "
    "entity_id, reference_snapshot, description, result, changes, detail, ip, device"
)


def list_events(
    *, module: str | None, action: str | None, result: str | None, user_id: str | None,
    date_from: datetime | None, date_to: datetime | None, limit: int, offset: int,
) -> dict[str, Any]:
    q = db().table("audit_events").select(AUDIT_SELECT, count="exact")
    if module:
        q = q.eq("module_key", module)
    if action:
        q = q.eq("action", action)
    if result:
        q = q.eq("result", result)
    if user_id:
        q = q.eq("user_id", user_id)
    if date_from:
        q = q.gte("occurred_at", date_from.isoformat())
    if date_to:
        q = q.lte("occurred_at", date_to.isoformat())
    res = execute(q.order("occurred_at", desc=True).range(offset, offset + limit - 1))
    return {"items": res.data or [], "total": res.count or 0, "limit": limit, "offset": offset}
