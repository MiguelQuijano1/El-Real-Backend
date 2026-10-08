from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.errors import bad_request, not_found
from app.repositories.client import db, execute, first
from app.security.deps import AuthUser
from app.services.audit import RequestMeta, record
from app.services import purchase_orders as po_svc

MODULE = "purchase-requests"

STATUS_UI = {
    "PENDING": "Pendiente",
    "APPROVED": "Aprobada",
    "ATTENDED": "Atendida",
    "REJECTED": "Rechazada",
}
UI_STATUS = {v: k for k, v in STATUS_UI.items()}
PRIORITY_UI = {"HIGH": "Alta", "MEDIUM": "Media", "LOW": "Baja"}
UI_PRIORITY = {v: k for k, v in PRIORITY_UI.items()}


def _next_code() -> str:
    y = datetime.now(timezone.utc).year
    n = execute(db().rpc("next_sequence", {"p_type": "SOL", "p_year": y, "p_series": ""})).data
    n = n[0] if isinstance(n, list) else n
    return f"SOL-{y}-{int(n):04d}"


def _embed() -> str:
    return (
        "*, "
        "lines:purchase_request_lines(*), "
        "warehouse:warehouses(id, code, name), "
        "supplier:suppliers(id, code, legal_name), "
        "requester:users!purchase_requests_requested_by_user_id_fkey(id, full_name)"
    )


def _serialize(row: dict[str, Any], order_code: str | None = None) -> dict[str, Any]:
    lines = sorted(row.get("lines") or [], key=lambda x: x.get("line_no", 0))
    warehouse = row.get("warehouse") or {}
    supplier = row.get("supplier") or {}
    requester = row.get("requester") or {}
    # fallback si el embed del requester falla
    if not requester and row.get("requested_by_user_id"):
        u = first(db().table("users").select("id, full_name").eq("id", row["requested_by_user_id"]))
        requester = u or {}
    status = row.get("status") or "PENDING"
    priority = row.get("priority") or "MEDIUM"
    items = [
        {
            "sku": ln.get("sku_snapshot") or "",
            "name": ln.get("name_snapshot"),
            "unit": ln.get("unit_snapshot"),
            "qty": float(ln.get("quantity") or 0),
            "price": float(ln.get("estimated_unit_cost") or 0),
            "productId": ln.get("product_id"),
        }
        for ln in lines
    ]
    solicitante = requester.get("full_name") or ""
    area = row.get("requesting_area") or ""
    motivo = row.get("reason") or ""
    data = {
        "solicitante": solicitante,
        "area": area,
        "motivo": motivo,
        "prioridad": PRIORITY_UI.get(priority, priority),
        "req": str(row.get("required_by") or ""),
        "alm": warehouse.get("name") or "",
        "prov": supplier.get("legal_name") or "",
    }
    return {
        "id": row["id"],
        "code": row["code"],
        "status": STATUS_UI.get(status, status),
        "statusKey": status,
        "createdAt": row.get("created_at"),
        "date": (row.get("created_at") or "")[:10],
        "party": solicitante,
        "partySub": f"{area} · {motivo}",
        "items": items,
        "data": data,
        "facts": [
            {"k": "Prioridad", "v": PRIORITY_UI.get(priority, priority)},
            {"k": "Requerido para", "v": str(row.get("required_by") or "")},
            {"k": "Almacén destino", "v": warehouse.get("name") or ""},
            {"k": "Proveedor sugerido", "v": supplier.get("legal_name") or "—"},
        ],
        "order": order_code,
        "warehouseId": row.get("warehouse_id"),
        "suggestedSupplierId": row.get("suggested_supplier_id"),
        "requestedByUserId": row.get("requested_by_user_id"),
    }


def _order_code(pr_id: str) -> str | None:
    o = first(db().table("business_orders").select("code").eq("purchase_request_id", pr_id))
    return o["code"] if o else None


def list_requests(*, q: str | None = None, status: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    query = db().table("purchase_requests").select(
        "*, lines:purchase_request_lines(*), warehouse:warehouses(id, code, name), supplier:suppliers(id, code, legal_name)",
        count="exact",
    )
    if status:
        key = UI_STATUS.get(status, status)
        if key in STATUS_UI:
            query = query.eq("status", key)
    if q:
        term = q.strip().replace(",", " ")
        if term:
            query = query.or_(f"code.ilike.%{term}%,requesting_area.ilike.%{term}%,reason.ilike.%{term}%")
    res = execute(query.order("created_at", desc=True).range(offset, offset + limit - 1))
    items = []
    for r in res.data or []:
        order_code = _order_code(r["id"]) if r.get("status") == "ATTENDED" else None
        items.append(_serialize(r, order_code))
    return {"items": items, "total": res.count or 0, "limit": limit, "offset": offset}


def get_request(id_or_code: str) -> dict[str, Any]:
    row = first(
        db().table("purchase_requests").select(
            "*, lines:purchase_request_lines(*), warehouse:warehouses(id, code, name), supplier:suppliers(id, code, legal_name)"
        ).eq("id", id_or_code)
    ) or first(
        db().table("purchase_requests").select(
            "*, lines:purchase_request_lines(*), warehouse:warehouses(id, code, name), supplier:suppliers(id, code, legal_name)"
        ).eq("code", id_or_code)
    )
    if not row:
        raise not_found("Solicitud no encontrada")
    order_code = _order_code(row["id"]) if row.get("status") == "ATTENDED" else None
    return _serialize(row, order_code)


def create_request(payload: dict[str, Any], actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    lines_in = payload.get("lines") or []
    if not lines_in:
        raise bad_request("La solicitud debe tener al menos una línea")
    priority = payload.get("priority") or "MEDIUM"
    if priority not in PRIORITY_UI:
        priority = UI_PRIORITY.get(priority, "MEDIUM")
    code = _next_code()
    row = {
        "code": code,
        "requested_by_user_id": actor.id,
        "requesting_area": payload["requesting_area"],
        "reason": payload["reason"],
        "priority": priority,
        "required_by": payload.get("required_by"),
        "warehouse_id": payload.get("warehouse_id"),
        "suggested_supplier_id": payload.get("suggested_supplier_id"),
        "status": "PENDING",
    }
    created = execute(db().table("purchase_requests").insert(row)).data[0]
    rid = created["id"]
    line_rows = [
        {
            "purchase_request_id": rid,
            "line_no": i,
            "product_id": ln.get("product_id"),
            "sku_snapshot": ln.get("sku"),
            "name_snapshot": ln["name"],
            "unit_snapshot": ln["unit"],
            "quantity": float(ln["quantity"]),
            "estimated_unit_cost": float(ln["estimated_unit_cost"]),
        }
        for i, ln in enumerate(lines_in, start=1)
    ]
    execute(db().table("purchase_request_lines").insert(line_rows))
    record(actor.actor, action="CREATE", module_key=MODULE, entity_type="PurchaseRequest",
           entity_id=rid, reference=code, description=f"Solicitud creada · {payload['reason'][:80]}", meta=meta)
    return get_request(rid)


def update_request(id_or_code: str, payload: dict[str, Any], actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    current = first(db().table("purchase_requests").select("*").eq("id", id_or_code)) or first(
        db().table("purchase_requests").select("*").eq("code", id_or_code)
    )
    if not current:
        raise not_found("Solicitud no encontrada")
    if current.get("status") not in ("PENDING",):
        raise bad_request("Solo se pueden editar solicitudes pendientes")
    patch: dict[str, Any] = {}
    for key in ("requesting_area", "reason", "required_by", "warehouse_id", "suggested_supplier_id"):
        if key in payload and payload[key] is not None:
            patch[key] = payload[key]
    if payload.get("priority"):
        p = payload["priority"]
        patch["priority"] = p if p in PRIORITY_UI else UI_PRIORITY.get(p, "MEDIUM")
    if patch:
        execute(db().table("purchase_requests").update(patch).eq("id", current["id"]))
    if payload.get("lines") is not None:
        lines_in = payload["lines"]
        if not lines_in:
            raise bad_request("La solicitud debe tener al menos una línea")
        execute(db().table("purchase_request_lines").delete().eq("purchase_request_id", current["id"]))
        line_rows = [
            {
                "purchase_request_id": current["id"],
                "line_no": i,
                "product_id": ln.get("product_id"),
                "sku_snapshot": ln.get("sku"),
                "name_snapshot": ln["name"],
                "unit_snapshot": ln["unit"],
                "quantity": float(ln["quantity"]),
                "estimated_unit_cost": float(ln["estimated_unit_cost"]),
            }
            for i, ln in enumerate(lines_in, start=1)
        ]
        execute(db().table("purchase_request_lines").insert(line_rows))
    record(actor.actor, action="EDIT", module_key=MODULE, entity_type="PurchaseRequest",
           entity_id=current["id"], reference=current["code"], description=f"Solicitud actualizada · {current['code']}", meta=meta)
    return get_request(current["id"])


def set_status(id_or_code: str, status: str, actor: AuthUser, meta: RequestMeta, rejection_reason: str | None = None) -> dict[str, Any]:
    current = first(db().table("purchase_requests").select("*").eq("id", id_or_code)) or first(
        db().table("purchase_requests").select("*").eq("code", id_or_code)
    )
    if not current:
        raise not_found("Solicitud no encontrada")
    key = UI_STATUS.get(status, status)
    if key not in ("PENDING", "APPROVED", "REJECTED"):
        raise bad_request("Estado inválido")
    if current.get("status") in ("ATTENDED",):
        raise bad_request("La solicitud ya fue atendida")
    patch: dict[str, Any] = {
        "status": key,
        "reviewed_by_user_id": actor.id,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    if key == "REJECTED":
        patch["rejection_reason"] = rejection_reason
    execute(db().table("purchase_requests").update(patch).eq("id", current["id"]))
    record(actor.actor, action="EDIT", module_key=MODULE, entity_type="PurchaseRequest",
           entity_id=current["id"], reference=current["code"],
           description=f"Solicitud · {STATUS_UI[key]}", meta=meta)
    return get_request(current["id"])


def delete_request(id_or_code: str, actor: AuthUser, meta: RequestMeta) -> None:
    current = first(db().table("purchase_requests").select("*").eq("id", id_or_code)) or first(
        db().table("purchase_requests").select("*").eq("code", id_or_code)
    )
    if not current:
        raise not_found("Solicitud no encontrada")
    if current.get("status") != "PENDING":
        raise bad_request("Solo se pueden eliminar solicitudes pendientes")
    execute(db().table("purchase_request_lines").delete().eq("purchase_request_id", current["id"]))
    execute(db().table("purchase_requests").delete().eq("id", current["id"]))
    record(actor.actor, action="DELETE", module_key=MODULE, entity_type="PurchaseRequest",
           entity_id=current["id"], reference=current["code"], description=f"Solicitud eliminada · {current['code']}", meta=meta)


def convert_request(id_or_code: str, payload: dict[str, Any], actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    pr = get_request(id_or_code)
    if pr["statusKey"] != "APPROVED":
        raise bad_request("Solo se puede convertir una solicitud aprobada")
    if pr.get("order"):
        raise bad_request("La solicitud ya tiene una orden asociada")
    raw = first(db().table("purchase_requests").select("*").eq("id", pr["id"]))
    if not raw:
        raise not_found("Solicitud no encontrada")
    supplier_id = payload["supplier_id"]
    supplier = first(db().table("suppliers").select("*").eq("id", supplier_id))
    if not supplier:
        raise bad_request("Proveedor no encontrado")
    warehouse_id = payload.get("warehouse_id") or raw.get("warehouse_id")
    if not warehouse_id:
        wh = first(db().table("warehouses").select("id").eq("status", "ACTIVE"))
        if not wh:
            raise bad_request("No hay almacén activo")
        warehouse_id = wh["id"]
    lines = [
        {
            "product_id": it.get("productId"),
            "sku": it.get("sku") or it["name"],
            "name": it["name"],
            "unit": it["unit"],
            "quantity": it["qty"],
            "unit_price": it["price"],
        }
        for it in pr["items"]
    ]
    order = po_svc.create_purchase_order(
        {
            "supplier_id": supplier_id,
            "warehouse_id": warehouse_id,
            "payment_terms_days": int(payload.get("payment_terms_days") or supplier.get("payment_terms_days") or 0),
            "party_name": supplier.get("legal_name"),
            "party_tax_id": supplier.get("tax_id"),
            "party_address": supplier.get("address"),
            "party_contact": supplier.get("contact_name"),
            "party_email": supplier.get("email"),
            "origin": pr["code"],
            "lines": lines,
        },
        actor,
        meta,
    )
    execute(db().table("business_orders").update({"purchase_request_id": raw["id"]}).eq("id", order["id"]))
    execute(db().table("purchase_requests").update({"status": "ATTENDED"}).eq("id", raw["id"]))
    record(actor.actor, action="EDIT", module_key=MODULE, entity_type="PurchaseRequest",
           entity_id=raw["id"], reference=raw["code"],
           description=f"Solicitud convertida en {order['code']}", meta=meta)
    return {"request": get_request(raw["id"]), "order": order}