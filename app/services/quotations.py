from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.errors import bad_request, not_found
from app.repositories.client import db, execute, first
from app.security.deps import AuthUser
from app.services.audit import RequestMeta, record
from app.services import orders as orders_svc

MODULE = "quotations"

STATUS_UI = {
    "DRAFT": "Borrador",
    "SENT": "Enviada",
    "APPROVED": "Aprobada",
    "CONVERTED": "Convertida",
    "REJECTED": "Rechazada",
}
UI_STATUS = {v: k for k, v in STATUS_UI.items()}


def _next_code() -> str:
    y = datetime.now(timezone.utc).year
    n = execute(db().rpc("next_sequence", {"p_type": "COT", "p_year": y, "p_series": ""})).data
    n = n[0] if isinstance(n, list) else n
    return f"COT-{y}-{int(n):04d}"


def _terms_label(days: int) -> str:
    return "Contado" if days <= 0 else f"Crédito {days} días"


def _days_from_term(label: str | None) -> int:
    if not label:
        return 0
    import re
    m = re.search(r"(\d+)", str(label))
    return int(m.group(1)) if m else 0


def _embed() -> str:
    return (
        "*, "
        "lines:quotation_lines(*), "
        "customer:customers(id, code, legal_name, document_type, document_number, contact_name, phone, billing_email, fiscal_address, payment_terms_days), "
        "warehouse:warehouses(id, code, name)"
    )


def _serialize(row: dict[str, Any], seller_name: str = "", order_code: str | None = None) -> dict[str, Any]:
    lines = sorted(row.get("lines") or [], key=lambda x: x.get("line_no", 0))
    customer = row.get("customer") or {}
    warehouse = row.get("warehouse") or {}
    terms_days = int(row.get("payment_terms_days") or 0)
    status = row.get("status") or "DRAFT"
    items = [
        {
            "sku": ln.get("sku_snapshot"),
            "name": ln.get("name_snapshot"),
            "unit": ln.get("unit_snapshot"),
            "qty": float(ln.get("quantity") or 0),
            "price": float(ln.get("unit_price") or 0),
            "productId": ln.get("product_id"),
        }
        for ln in lines
    ]
    data = {
        "cliente": customer.get("legal_name") or "",
        "vendedor": seller_name,
        "valida": str(row.get("valid_until") or ""),
        "cond": _terms_label(terms_days),
        "alm": warehouse.get("name") or "",
    }
    party_sub = ""
    if customer.get("document_number"):
        party_sub = f"{customer.get('document_type') or 'RUC'} {customer.get('document_number')}"
    return {
        "id": row["id"],
        "code": row["code"],
        "status": STATUS_UI.get(status, status),
        "statusKey": status,
        "createdAt": row.get("created_at"),
        "date": (row.get("created_at") or "")[:10],
        "validUntil": row.get("valid_until"),
        "customerId": row.get("customer_id"),
        "warehouseId": row.get("warehouse_id"),
        "sellerUserId": row.get("seller_user_id"),
        "seller": seller_name,
        "party": customer.get("legal_name") or "",
        "partySub": party_sub,
        "items": items,
        "data": data,
        "facts": [
            {"k": "Vendedor", "v": seller_name},
            {"k": "Válida hasta", "v": str(row.get("valid_until") or "")},
            {"k": "Condición", "v": _terms_label(terms_days)},
            {"k": "Entrega", "v": warehouse.get("name") or ""},
        ],
        "order": order_code,
        "paymentTermsDays": terms_days,
        "rejectedReason": row.get("rejected_reason"),
    }


def _seller_names(ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    return {
        u["id"]: u.get("full_name") or ""
        for u in (execute(db().table("users").select("id, full_name").in_("id", list(ids))).data or [])
    }


def _order_code_for_quotation(quotation_id: str) -> str | None:
    o = first(db().table("business_orders").select("code").eq("quotation_id", quotation_id))
    return o["code"] if o else None


def list_quotations(*, q: str | None = None, status: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    query = db().table("quotations").select(_embed(), count="exact")
    if status:
        key = UI_STATUS.get(status, status)
        if key in STATUS_UI:
            query = query.eq("status", key)
    if q:
        term = q.strip().replace(",", " ")
        if term:
            query = query.or_(f"code.ilike.%{term}%")
    res = execute(query.order("created_at", desc=True).range(offset, offset + limit - 1))
    rows = res.data or []
    names = _seller_names({r.get("seller_user_id") for r in rows if r.get("seller_user_id")})
    items = []
    for r in rows:
        order_code = _order_code_for_quotation(r["id"]) if r.get("status") == "CONVERTED" else None
        items.append(_serialize(r, names.get(r.get("seller_user_id") or "", ""), order_code))
    return {"items": items, "total": res.count or 0, "limit": limit, "offset": offset}


def get_quotation(id_or_code: str) -> dict[str, Any]:
    row = first(db().table("quotations").select(_embed()).eq("id", id_or_code))
    if not row:
        row = first(db().table("quotations").select(_embed()).eq("code", id_or_code))
    if not row:
        raise not_found("Cotización no encontrada")
    seller = ""
    if row.get("seller_user_id"):
        u = first(db().table("users").select("full_name").eq("id", row["seller_user_id"]))
        seller = (u or {}).get("full_name") or ""
    order_code = _order_code_for_quotation(row["id"]) if row.get("status") == "CONVERTED" else None
    return _serialize(row, seller, order_code)


def create_quotation(payload: dict[str, Any], actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    customer = first(db().table("customers").select("*").eq("id", payload["customer_id"]))
    if not customer:
        raise bad_request("Cliente no encontrado")
    lines_in = payload.get("lines") or []
    if not lines_in:
        raise bad_request("La cotización debe tener al menos una línea")

    warehouse_id = payload.get("warehouse_id")
    if warehouse_id and not first(db().table("warehouses").select("id").eq("id", warehouse_id)):
        raise bad_request("Almacén no encontrado")

    status = payload.get("status") or "DRAFT"
    if status not in ("DRAFT", "SENT"):
        status = "DRAFT"

    code = _next_code()
    row = {
        "code": code,
        "customer_id": payload["customer_id"],
        "seller_user_id": payload.get("seller_user_id") or actor.id,
        "warehouse_id": warehouse_id,
        "valid_until": payload.get("valid_until"),
        "payment_terms_days": int(payload.get("payment_terms_days") or customer.get("payment_terms_days") or 0),
        "status": status,
    }
    created = execute(db().table("quotations").insert(row)).data[0]
    qid = created["id"]
    line_rows = [
        {
            "quotation_id": qid,
            "line_no": i,
            "product_id": ln.get("product_id"),
            "sku_snapshot": ln["sku"],
            "name_snapshot": ln["name"],
            "unit_snapshot": ln["unit"],
            "quantity": float(ln["quantity"]),
            "unit_price": float(ln["unit_price"]),
        }
        for i, ln in enumerate(lines_in, start=1)
    ]
    execute(db().table("quotation_lines").insert(line_rows))
    record(
        actor.actor,
        action="CREATE",
        module_key=MODULE,
        entity_type="Quotation",
        entity_id=qid,
        reference=code,
        description=f"Cotización creada · {customer.get('legal_name')}",
        meta=meta,
    )
    return get_quotation(qid)


def update_quotation(id_or_code: str, payload: dict[str, Any], actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    current = first(db().table("quotations").select("*").eq("id", id_or_code)) or first(
        db().table("quotations").select("*").eq("code", id_or_code)
    )
    if not current:
        raise not_found("Cotización no encontrada")
    if current.get("status") in ("CONVERTED", "REJECTED"):
        raise bad_request("No se puede editar una cotización convertida o rechazada")

    patch: dict[str, Any] = {}
    for key in ("customer_id", "seller_user_id", "warehouse_id", "valid_until", "payment_terms_days", "rejected_reason"):
        if key in payload and payload[key] is not None:
            patch[key] = payload[key]
    if payload.get("status") and payload["status"] in ("DRAFT", "SENT", "APPROVED", "REJECTED"):
        if current.get("status") == "CONVERTED":
            raise bad_request("La cotización ya fue convertida")
        patch["status"] = payload["status"]

    if patch:
        execute(db().table("quotations").update(patch).eq("id", current["id"]))

    if payload.get("lines") is not None:
        lines_in = payload["lines"]
        if not lines_in:
            raise bad_request("La cotización debe tener al menos una línea")
        execute(db().table("quotation_lines").delete().eq("quotation_id", current["id"]))
        line_rows = [
            {
                "quotation_id": current["id"],
                "line_no": i,
                "product_id": ln.get("product_id"),
                "sku_snapshot": ln["sku"],
                "name_snapshot": ln["name"],
                "unit_snapshot": ln["unit"],
                "quantity": float(ln["quantity"]),
                "unit_price": float(ln["unit_price"]),
            }
            for i, ln in enumerate(lines_in, start=1)
        ]
        execute(db().table("quotation_lines").insert(line_rows))

    record(
        actor.actor,
        action="EDIT",
        module_key=MODULE,
        entity_type="Quotation",
        entity_id=current["id"],
        reference=current["code"],
        description=f"Cotización actualizada · {current['code']}",
        meta=meta,
    )
    return get_quotation(current["id"])


def set_status(id_or_code: str, status: str, actor: AuthUser, meta: RequestMeta, rejected_reason: str | None = None) -> dict[str, Any]:
    return update_quotation(
        id_or_code,
        {"status": status, "rejected_reason": rejected_reason},
        actor,
        meta,
    )


def delete_quotation(id_or_code: str, actor: AuthUser, meta: RequestMeta) -> None:
    current = first(db().table("quotations").select("*").eq("id", id_or_code)) or first(
        db().table("quotations").select("*").eq("code", id_or_code)
    )
    if not current:
        raise not_found("Cotización no encontrada")
    if current.get("status") not in ("DRAFT",):
        raise bad_request("Solo se pueden eliminar cotizaciones en borrador")
    execute(db().table("quotation_lines").delete().eq("quotation_id", current["id"]))
    execute(db().table("quotations").delete().eq("id", current["id"]))
    record(
        actor.actor,
        action="DELETE",
        module_key=MODULE,
        entity_type="Quotation",
        entity_id=current["id"],
        reference=current["code"],
        description=f"Cotización eliminada · {current['code']}",
        meta=meta,
    )


def convert_quotation(id_or_code: str, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    """Convierte una cotización APROBADA en orden de venta."""
    q = get_quotation(id_or_code)
    if q["statusKey"] != "APPROVED":
        raise bad_request("Solo se puede convertir una cotización aprobada")
    if q.get("order"):
        raise bad_request("La cotización ya tiene una orden asociada")

    raw = first(db().table("quotations").select("*").eq("id", q["id"]))
    if not raw:
        raise not_found("Cotización no encontrada")

    lines = [
        {
            "product_id": it.get("productId"),
            "sku": it["sku"],
            "name": it["name"],
            "unit": it["unit"],
            "quantity": it["qty"],
            "unit_price": it["price"],
        }
        for it in q["items"]
    ]
    customer = first(db().table("customers").select("*").eq("id", raw["customer_id"])) or {}
    order_payload = {
        "customer_id": raw["customer_id"],
        "warehouse_id": raw.get("warehouse_id")
        or (first(db().table("company_settings").select("default_warehouse_id").eq("id", 1)) or {}).get("default_warehouse_id"),
        "seller_user_id": raw.get("seller_user_id") or actor.id,
        "payment_terms_days": int(raw.get("payment_terms_days") or 0),
        "party_name": customer.get("legal_name"),
        "party_tax_id": customer.get("document_number"),
        "party_address": customer.get("fiscal_address"),
        "party_contact": customer.get("contact_name"),
        "party_email": customer.get("billing_email"),
        "origin": q["code"],
        "lines": lines,
    }
    if not order_payload["warehouse_id"]:
        wh = first(db().table("warehouses").select("id").eq("status", "ACTIVE"))
        if not wh:
            raise bad_request("No hay almacén activo para crear la orden")
        order_payload["warehouse_id"] = wh["id"]

    # Crear orden y vincular quotation_id
    order = orders_svc.create_sale_order(order_payload, actor, meta)
    execute(
        db()
        .table("business_orders")
        .update({"quotation_id": raw["id"]})
        .eq("id", order["id"])
    )
    execute(db().table("quotations").update({"status": "CONVERTED"}).eq("id", raw["id"]))
    record(
        actor.actor,
        action="EDIT",
        module_key=MODULE,
        entity_type="Quotation",
        entity_id=raw["id"],
        reference=raw["code"],
        description=f"Cotización convertida en {order['code']}",
        meta=meta,
    )
    return {"quotation": get_quotation(raw["id"]), "order": order}