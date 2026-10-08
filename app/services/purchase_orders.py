from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.errors import bad_request, not_found
from app.repositories.client import db, execute, first
from app.security.deps import AuthUser
from app.services.audit import RequestMeta, record

MODULE = "purchase-orders"
KIND = "PURCHASE"
MAX_STEP = 5


def _next_code(prefix: str) -> str:
    y = datetime.now(timezone.utc).year
    n = execute(db().rpc("next_sequence", {"p_type": prefix, "p_year": y, "p_series": ""})).data
    n = n[0] if isinstance(n, list) else n
    return f"{prefix}-{y}-{int(n):04d}"


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
        "lines:order_lines(*), "
        "events:order_step_events(*), "
        "warehouse:warehouses(id, code, name), "
        "supplier:suppliers(id, code, legal_name, tax_id, contact_name, phone, email, address, payment_terms_days)"
    )


def _serialize(row: dict[str, Any], seller_name: str = "") -> dict[str, Any]:
    lines = sorted(row.get("lines") or [], key=lambda x: x.get("line_no", 0))
    events = sorted(row.get("events") or [], key=lambda x: x.get("step_no", 0))
    supplier = row.get("supplier") or {}
    warehouse = row.get("warehouse") or {}
    forms: dict[str, dict[str, Any]] = {}
    docs: dict[str, str] = {}
    log: dict[str, dict[str, Any]] = {}
    for ev in events:
        step = int(ev.get("step_no") or 0)
        key = str(step)
        fd = ev.get("form_data")
        if isinstance(fd, dict):
            forms[key] = {str(k): str(v) if v is not None else "" for k, v in fd.items()}
        else:
            forms[key] = {}
        docs[key] = str(ev.get("generated_reference") or "")
        log[key] = {"who": "", "at": ev.get("completed_at"), "userId": ev.get("completed_by_user_id")}
    terms_days = int(row.get("payment_terms_days") or 0)
    party = {
        "code": supplier.get("code") or "",
        "name": row.get("party_name") or supplier.get("legal_name") or "",
        "ruc": row.get("party_tax_id") or supplier.get("tax_id") or "",
        "contact": row.get("party_contact") or supplier.get("contact_name") or "",
        "terms": _terms_label(terms_days),
        "address": row.get("party_address") or supplier.get("address") or "",
        "email": row.get("party_email") or supplier.get("email") or "",
    }
    return {
        "id": row["id"],
        "code": row["code"],
        "kind": "compra",
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
        "supplierId": row.get("supplier_id"),
        "warehouseId": row.get("warehouse_id"),
        "warehouse": warehouse.get("name") or "",
        "sellerUserId": row.get("seller_user_id"),
        "seller": seller_name,
        "party": party,
        "lines": [
            {
                "sku": ln.get("sku_snapshot"),
                "name": ln.get("name_snapshot"),
                "unit": ln.get("unit_snapshot"),
                "qty": float(ln.get("quantity") or 0),
                "price": float(ln.get("unit_price") or 0),
                "productId": ln.get("product_id"),
            }
            for ln in lines
        ],
        "done": int(row.get("current_step") or 1),
        "forms": forms,
        "docs": docs,
        "log": log,
        "igvRate": float(row.get("tax_rate") if row.get("tax_rate") is not None else 0.18),
        "cancelled": bool(row.get("is_cancelled")),
        "origin": None,
        "paymentTermsDays": terms_days,
    }


def list_purchase_orders(*, q: str | None = None, cancelled: bool | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    query = db().table("business_orders").select(_embed(), count="exact").eq("kind", KIND)
    if cancelled is True:
        query = query.eq("is_cancelled", True)
    elif cancelled is False:
        query = query.eq("is_cancelled", False)
    if q:
        term = q.strip().replace(",", " ")
        if term:
            query = query.or_(f"code.ilike.%{term}%,party_name.ilike.%{term}%,party_tax_id.ilike.%{term}%")
    res = execute(query.order("created_at", desc=True).range(offset, offset + limit - 1))
    rows = res.data or []
    seller_ids = {r.get("seller_user_id") for r in rows if r.get("seller_user_id")}
    names: dict[str, str] = {}
    if seller_ids:
        for u in execute(db().table("users").select("id, full_name").in_("id", list(seller_ids))).data or []:
            names[u["id"]] = u.get("full_name") or ""
    items = [_serialize(r, names.get(r.get("seller_user_id") or "")) for r in rows]
    return {"items": items, "total": res.count or 0, "limit": limit, "offset": offset}


def get_purchase_order(order_id_or_code: str) -> dict[str, Any]:
    row = first(db().table("business_orders").select(_embed()).eq("id", order_id_or_code).eq("kind", KIND)) or first(
        db().table("business_orders").select(_embed()).eq("code", order_id_or_code).eq("kind", KIND)
    )
    if not row:
        raise not_found("Orden de compra no encontrada")
    seller_name = ""
    if row.get("seller_user_id"):
        u = first(db().table("users").select("full_name").eq("id", row["seller_user_id"]))
        seller_name = (u or {}).get("full_name") or ""
    return _serialize(row, seller_name)


def create_purchase_order(payload: dict[str, Any], actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    supplier_id = payload["supplier_id"]
    warehouse_id = payload["warehouse_id"]
    lines_in = payload.get("lines") or []
    if not lines_in:
        raise bad_request("La orden debe tener al menos una línea")
    supplier = first(db().table("suppliers").select("*").eq("id", supplier_id))
    if not supplier:
        raise bad_request("Proveedor no encontrado")
    warehouse = first(db().table("warehouses").select("id, name, status").eq("id", warehouse_id))
    if not warehouse:
        raise bad_request("Almacén no encontrado")
    terms_days = int(payload.get("payment_terms_days") if payload.get("payment_terms_days") is not None else supplier.get("payment_terms_days") or 0)
    tax_rate = payload.get("tax_rate")
    if tax_rate is None:
        settings = first(db().table("company_settings").select("tax_rate").eq("id", 1))
        tax_rate = (settings or {}).get("tax_rate", 0.18)
    code = _next_code("OC")
    order_row = {
        "code": code,
        "kind": KIND,
        "supplier_id": supplier_id,
        "warehouse_id": warehouse_id,
        "seller_user_id": payload.get("seller_user_id") or actor.id,
        "created_by_user_id": actor.id,
        "party_name": payload.get("party_name") or supplier.get("legal_name"),
        "party_tax_id": payload.get("party_tax_id") or supplier.get("tax_id"),
        "party_address": payload.get("party_address") or supplier.get("address"),
        "party_contact": payload.get("party_contact") or supplier.get("contact_name"),
        "party_email": payload.get("party_email") or supplier.get("email"),
        "payment_terms_days": terms_days,
        "tax_rate": float(tax_rate),
        "current_step": 1,
        "is_cancelled": False,
    }
    created = execute(db().table("business_orders").insert(order_row)).data[0]
    order_id = created["id"]
    line_rows = [
        {
            "order_id": order_id,
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
    execute(db().table("order_lines").insert(line_rows))
    origin = payload.get("origin") or "Compra directa"
    now = datetime.now(timezone.utc).isoformat()
    event = {
        "order_id": order_id,
        "step_no": 0,
        "completed_by_user_id": actor.id,
        "completed_at": now,
        "generated_reference": origin,
        "note": origin,
    }
    try:
        execute(db().table("order_step_events").insert({**event, "form_data": {}}))
    except Exception:
        execute(db().table("order_step_events").insert(event))
    record(actor.actor, action="CREATE", module_key=MODULE, entity_type="BusinessOrder",
           entity_id=order_id, reference=code, description=f"Orden de compra · {order_row['party_name']}", meta=meta)
    return get_purchase_order(order_id)


def complete_step(order_id_or_code: str, payload: dict[str, Any], actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    order = first(db().table("business_orders").select("*").eq("id", order_id_or_code).eq("kind", KIND)) or first(
        db().table("business_orders").select("*").eq("code", order_id_or_code).eq("kind", KIND)
    )
    if not order:
        raise not_found("Orden de compra no encontrada")
    if order.get("is_cancelled"):
        raise bad_request("La orden está anulada")
    step = int(order.get("current_step") or 1)
    if step >= MAX_STEP:
        raise bad_request("La orden ya completó todos los pasos")
    values = payload.get("values") or {}
    values_str = {str(k): "" if v is None else str(v) for k, v in values.items()}
    generated = payload.get("generated_reference")
    note = payload.get("note")
    patch: dict[str, Any] = {"current_step": step + 1}
    if step == 1:
        # Aprobar: almacén / condición
        if values_str.get("alm"):
            wh = first(db().table("warehouses").select("id, name").eq("name", values_str["alm"]))
            if wh:
                patch["warehouse_id"] = wh["id"]
        if values_str.get("cond"):
            patch["payment_terms_days"] = _days_from_term(values_str["cond"])
        generated = generated or order["code"]
    elif step == 2:
        generated = generated or _create_goods_receipt(order, values_str, actor)
    elif step == 3:
        generated = generated or _create_supplier_invoice(order, values_str, actor)
    elif step == 4:
        generated = generated or f"Pago-{order['code']}"
    execute(db().table("business_orders").update(patch).eq("id", order["id"]))
    now = datetime.now(timezone.utc).isoformat()
    event = {
        "order_id": order["id"],
        "step_no": step,
        "completed_by_user_id": actor.id,
        "completed_at": now,
        "generated_reference": generated,
        "note": note or (str(values_str) if values_str else None),
    }
    try:
        execute(db().table("order_step_events").insert({**event, "form_data": values_str}))
    except Exception:
        execute(db().table("order_step_events").insert(event))
    record(actor.actor, action="EDIT", module_key=MODULE, entity_type="BusinessOrder",
           entity_id=order["id"], reference=order["code"],
           description=f"Paso {step} completado · {generated or order['code']}", meta=meta,
           changes=[("current_step", str(step), str(step + 1))])
    return get_purchase_order(order["id"])



def _post_receipt_movements(order: dict[str, Any], receipt_id: str | None, warehouse_id: str, actor: AuthUser) -> None:
    lines = execute(db().table("order_lines").select("*").eq("order_id", order["id"]).order("line_no")).data or []
    now = datetime.now(timezone.utc).isoformat()
    for ln in lines:
        try:
            execute(db().table("inventory_movements").insert({
                "occurred_at": now,
                "product_id": ln.get("product_id"),
                "warehouse_id": warehouse_id,
                "movement_type": "RECEIPT",
                "quantity_delta": float(ln["quantity"]),
                "unit_cost": float(ln.get("unit_price") or 0),
                "order_line_id": ln["id"],
                "goods_receipt_id": receipt_id,
                "reference_snapshot": order["code"],
                "created_by_user_id": actor.id,
            }))
        except Exception:
            pass


def _create_goods_receipt(order: dict[str, Any], values: dict[str, str], actor: AuthUser) -> str:
    receipt_number = _next_code("NI")
    condition = "CONFORMING"
    if values.get("est") and "observ" in values["est"].lower():
        condition = "OBSERVATIONS"
    warehouse_id = order["warehouse_id"]
    if values.get("alm"):
        wh = first(db().table("warehouses").select("id").eq("name", values["alm"]))
        if wh:
            warehouse_id = wh["id"]
            execute(db().table("business_orders").update({"warehouse_id": warehouse_id}).eq("id", order["id"]))
    received_at = values.get("fr") or datetime.now(timezone.utc).date().isoformat()
    row = {
        "order_id": order["id"],
        "receipt_number": receipt_number,
        "received_at": received_at if "T" in received_at else f"{received_at}T12:00:00+00:00",
        "supplier_guide_reference": values.get("gr") or None,
        "warehouse_id": warehouse_id,
        "condition": condition,
        "observations": values.get("obs") or None,
        "received_by_user_id": actor.id,
    }
    receipt_id = None
    try:
        created = execute(db().table("goods_receipts").insert(row)).data[0]
        receipt_id = created.get("id")
    except Exception:
        pass
    _post_receipt_movements(order, receipt_id, warehouse_id, actor)
    return f"Ingreso {receipt_number}"


def _create_supplier_invoice(order: dict[str, Any], values: dict[str, str], actor: AuthUser) -> str:
    series_number = values.get("num") or f"F002-{int(datetime.now(timezone.utc).timestamp()) % 100000:05d}"
    issued = values.get("fe") or datetime.now(timezone.utc).date().isoformat()
    due = values.get("fv") or issued
    total = float(values.get("mon") or 0)
    if total <= 0:
        lines = execute(db().table("order_lines").select("*").eq("order_id", order["id"])).data or []
        sub = sum(float(ln["quantity"]) * float(ln["unit_price"]) for ln in lines)
        total = round(sub * (1 + float(order.get("tax_rate") or 0.18)), 2)
    validation = "MATCHES_ORDER_RECEIPT"
    if values.get("val") and "difer" in values["val"].lower():
        validation = "HAS_DIFFERENCES"
    row = {
        "order_id": order["id"],
        "supplier_id": order["supplier_id"],
        "series_number": series_number,
        "issued_at": issued,
        "due_at": due,
        "currency": "PEN",
        "total": total,
        "validation_status": validation,
        "created_by_user_id": actor.id,
    }
    try:
        execute(db().table("supplier_invoices").insert(row))
    except Exception:
        pass
    return f"Factura {series_number}"


def cancel_purchase_order(order_id_or_code: str, actor: AuthUser, meta: RequestMeta, reason: str | None = None) -> dict[str, Any]:
    order = first(db().table("business_orders").select("*").eq("id", order_id_or_code).eq("kind", KIND)) or first(
        db().table("business_orders").select("*").eq("code", order_id_or_code).eq("kind", KIND)
    )
    if not order:
        raise not_found("Orden de compra no encontrada")
    if order.get("is_cancelled"):
        raise bad_request("La orden ya está anulada")
    if int(order.get("current_step") or 1) >= MAX_STEP:
        raise bad_request("No se puede anular una orden ya pagada")
    now = datetime.now(timezone.utc).isoformat()
    execute(db().table("business_orders").update({
        "is_cancelled": True, "cancelled_at": now, "cancelled_by_user_id": actor.id,
    }).eq("id", order["id"]))
    record(actor.actor, action="EDIT", module_key=MODULE, entity_type="BusinessOrder",
           entity_id=order["id"], reference=order["code"],
           description=f"Orden de compra anulada{(' · ' + reason) if reason else ''}", meta=meta)
    return get_purchase_order(order["id"])