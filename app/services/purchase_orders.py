from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.errors import bad_request, conflict, forbidden, not_found
from app.repositories.client import db, execute, find_by_id_or_code, first
from app.services.costing import get_average_cost, product_stock_total, set_average_cost, weighted_average
from app.services.permissions import role_has_permission
from app.services.finance import cash_balance, register_supplier_payment, delete_payment
from app.security.deps import AuthUser
from app.services.audit import RequestMeta, record

MODULE = "purchase-orders"
MODULE_RECEIPTS = "goods-receipts"
KIND = "PURCHASE"
MAX_STEP = 5
# Pasos de la orden de compra (current_step = pasos completados):
#   1 registrada · 2 orden lista para recepción (así nace) · 3 recibida · 4 factura registrada · 5 pagada.
# La orden NO pide aprobación al crearse: nace en el paso 2 y se aprueba/recibe en Kardex (approve_receipt).
STEP_READY = 2


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


def _event_user_names(rows_: list[dict[str, Any]]) -> dict[str, str]:
    """Nombre de quien completó cada paso (para el historial de la orden)."""
    ids = {ev.get("completed_by_user_id") for r in rows_ for ev in (r.get("events") or []) if ev.get("completed_by_user_id")}
    if not ids:
        return {}
    found = execute(db().table("users").select("id, full_name").in_("id", list(ids))).data or []
    return {u["id"]: u.get("full_name") or "" for u in found}


def _serialize(row: dict[str, Any], seller_name: str = "", user_names: dict[str, str] | None = None) -> dict[str, Any]:
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
        uid = ev.get("completed_by_user_id")
        log[key] = {"who": (user_names or {}).get(uid or "", ""), "at": ev.get("completed_at"), "userId": uid}
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
    actors = _event_user_names(rows)
    items = [_serialize(r, names.get(r.get("seller_user_id") or ""), actors) for r in rows]
    return {"items": items, "total": res.count or 0, "limit": limit, "offset": offset}


def get_purchase_order(order_id_or_code: str) -> dict[str, Any]:
    row = find_by_id_or_code("business_orders", order_id_or_code, _embed(), kind=KIND)
    if not row:
        raise not_found("Orden de compra no encontrada")
    seller_name = ""
    if row.get("seller_user_id"):
        u = first(db().table("users").select("full_name").eq("id", row["seller_user_id"]))
        seller_name = (u or {}).get("full_name") or ""
    return _serialize(row, seller_name, _event_user_names([row]))


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
        "current_step": STEP_READY,
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
    # Paso 1 (orden registrada): la orden queda lista para recepción, sin aprobación aparte.
    ready_event = {**event, "step_no": 1, "generated_reference": code, "note": "Orden de compra registrada"}
    for ev in (event, ready_event):
        try:
            execute(db().table("order_step_events").insert({**ev, "form_data": {}}))
        except Exception:
            execute(db().table("order_step_events").insert(ev))
    total = sum(float(ln["quantity"]) * float(ln["unit_price"]) for ln in lines_in) * (1 + float(tax_rate))
    record(actor.actor, action="CREATE", module_key=MODULE, entity_type="BusinessOrder",
           entity_id=order_id, reference=code,
           description=f"Orden de compra creada · {order_row['party_name']} · S/ {total:.2f} · pendiente de recepción en Kardex",
           meta=meta)
    return get_purchase_order(order_id)


def complete_step(order_id_or_code: str, payload: dict[str, Any], actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    order = find_by_id_or_code("business_orders", order_id_or_code, kind=KIND)
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
        # La recepción, la factura con su archivo y el egreso se aprueban juntos en Kardex (approve_receipt).
        raise bad_request("La recepción de mercadería se aprueba en Kardex")
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



def _restore_costs(restore: list[tuple[str, float]]) -> None:
    for pid, old_avg in reversed(restore):
        set_average_cost(pid, old_avg)


def _post_receipt_movements(
    order: dict[str, Any], receipt_id: str | None, warehouse_id: str, actor: AuthUser,
    received: dict[str, float] | None = None,
) -> list[tuple[str, float]]:
    """Entradas de inventario de la recepción. `received`: línea → cantidad recibida (por defecto, la pedida).

    Devuelve los costos promedio anteriores para que el llamador los restituya si un paso posterior falla.
    """
    lines = execute(db().table("order_lines").select("*").eq("order_id", order["id"]).order("line_no")).data or []
    now = datetime.now(timezone.utc).isoformat()
    restore: list[tuple[str, float]] = []  # costos promedio a devolver si una línea posterior falla
    try:
        for ln in lines:
            if not ln.get("product_id"):
                continue  # línea sin producto del catálogo: no mueve stock
            pid, cost = ln["product_id"], float(ln.get("unit_price") or 0)
            qty = float(received[ln["id"]]) if received and ln["id"] in received else float(ln["quantity"])
            if qty <= 0:
                continue  # nada recibido de esta línea
            old_avg = get_average_cost(pid)
            new_avg = weighted_average(product_stock_total(pid), old_avg, qty, cost)  # antes de insertar la entrada
            execute(db().table("inventory_movements").insert({
                "occurred_at": now,
                "product_id": pid,
                "warehouse_id": warehouse_id,
                "movement_type": "RECEIPT",
                "quantity_delta": qty,
                "unit_cost": cost,
                "order_line_id": ln["id"],
                "goods_receipt_id": receipt_id,
                "reference_snapshot": order["code"],
                "created_by_user_id": actor.id,
            }))
            if new_avg != old_avg:
                set_average_cost(pid, new_avg)
                restore.append((pid, old_avg))
    except Exception:
        _restore_costs(restore)
        raise
    return restore


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
    # Sin try/except: si la factura no se guarda, el paso no debe avanzar.
    execute(db().table("supplier_invoices").insert(row))
    return f"Factura {series_number}"


def _money(v: Any) -> float:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return 0.0


def _insert_event(order_id: str, step_no: int, actor: AuthUser, reference: str, values: dict[str, str], note: str | None = None) -> None:
    event = {
        "order_id": order_id,
        "step_no": step_no,
        "completed_by_user_id": actor.id,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "generated_reference": reference,
        "note": note,
    }
    try:
        execute(db().table("order_step_events").insert({**event, "form_data": values}))
    except Exception:
        execute(db().table("order_step_events").insert(event))


def approve_receipt(order_id_or_code: str, payload: dict[str, Any], actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    """Aprueba en Kardex la recepción de una orden de compra, como UNA sola operación:

      nota de ingreso + entradas de inventario  →  factura del proveedor (con archivo)  →  pago (egreso de caja o banco).

    - Solo corresponde a órdenes en el paso 2 (listas para recepción); el paso se "reclama" con un UPDATE condicional,
      así que una segunda petición simultánea o repetida recibe 409 y no duplica entradas ni pagos.
    - Todo lo validable se valida antes de escribir (cantidades, factura duplicada, saldo de caja).
    - Si un paso falla, se revierten los anteriores y la orden vuelve al paso 2.
    - El pago se registra aquí solo cuando la condición de pago es Contado (o `pay_now`); a crédito queda la cuenta por
      pagar pendiente (paso 4) y el egreso se hace cuando se paga la factura.
    """
    order = find_by_id_or_code("business_orders", order_id_or_code, kind=KIND)
    if not order:
        raise not_found("Orden de compra no encontrada")
    if order.get("is_cancelled"):
        raise bad_request("La orden está anulada")
    step = int(order.get("current_step") or 1)
    # Las órdenes anteriores que quedaron «por aprobar» (paso 1) se tratan como listas para recepción: ya no hay aprobación aparte.
    if step > STEP_READY:
        raise conflict("La orden ya fue recibida; no se puede aprobar la recepción otra vez")

    order_lines = execute(db().table("order_lines").select("*").eq("order_id", order["id"]).order("line_no")).data or []
    if not order_lines:
        raise bad_request("La orden no tiene productos")

    # --- cantidades recibidas vs. pedidas
    given: dict[str, float] = {}
    for item in payload.get("lines") or []:
        if item.get("sku") is not None and item.get("quantity") is not None:
            given[str(item["sku"])] = float(item["quantity"])
    received: dict[str, float] = {}
    differences: list[str] = []
    for ln in order_lines:
        ordered = float(ln["quantity"])
        qty = given.get(str(ln.get("sku_snapshot")), ordered)
        if qty < 0 or qty > ordered + 1e-9:
            raise bad_request(f"La cantidad recibida de {ln.get('name_snapshot')} debe estar entre 0 y {ordered:g}")
        received[ln["id"]] = qty
        if abs(qty - ordered) > 1e-9:
            differences.append(f"{ln.get('sku_snapshot')}: recibido {qty:g} de {ordered:g}")
    if not any(q > 0 for q in received.values()):
        raise bad_request("Indica al menos una cantidad recibida mayor a 0")

    # --- factura del proveedor
    tax_rate = float(order.get("tax_rate") if order.get("tax_rate") is not None else 0.18)
    ordered_total = _money(sum(float(ln["quantity"]) * float(ln["unit_price"]) for ln in order_lines) * (1 + tax_rate))
    received_total = _money(sum(received[ln["id"]] * float(ln["unit_price"]) for ln in order_lines) * (1 + tax_rate))
    series_number = str(payload.get("invoice_number") or "").strip()
    invoice_type = str(payload.get("invoice_type") or "Factura").strip() or "Factura"
    invoice_label = f"{invoice_type} {series_number}"
    if not series_number:
        raise bad_request("Ingresa la serie y número de la factura del proveedor")
    invoice_total = _money(payload.get("invoice_total")) or received_total
    if invoice_total <= 0:
        raise bad_request("El monto de la factura debe ser mayor a 0")
    if first(db().table("supplier_invoices").select("id").eq("supplier_id", order["supplier_id"]).eq("series_number", series_number)):
        raise bad_request(f"La factura {series_number} ya está registrada para este proveedor")
    if first(db().table("supplier_invoices").select("id").eq("order_id", order["id"])):
        raise conflict("La orden ya tiene una factura registrada")
    # El archivo del comprobante es opcional; si se envía, debe existir.
    document_id = payload.get("document_id") or None
    if document_id and not first(db().table("documents").select("id").eq("id", document_id).is_("deleted_at", "null")):
        raise bad_request("El archivo del comprobante no existe o fue eliminado")

    # --- pago (egreso)
    terms_days = int(order.get("payment_terms_days") or 0)
    pay_now = payload.get("pay_now")
    pay_now = (terms_days <= 0) if pay_now is None else bool(pay_now)
    channel = str(payload.get("payment_channel") or "CASH").upper()
    channel = channel if channel in ("CASH", "BANK") else "CASH"
    # El egreso es una operación de Finanzas: quien aprueba la recepción también debe poder registrar pagos.
    if pay_now and not actor.is_admin and not role_has_permission(actor.role_id, "payables", "edit"):
        raise forbidden("No tienes permiso para registrar el pago de la factura (Cuentas por pagar)")
    if pay_now and channel == "CASH":
        available = cash_balance()
        if invoice_total > available + 0.01:
            raise bad_request(f"Saldo de caja insuficiente para pagar {invoice_total:.2f} (disponible {available:.2f})")

    warehouse_id = order["warehouse_id"]
    wh_name = str(payload.get("warehouse") or "").strip()
    if wh_name:
        wh = first(db().table("warehouses").select("id").eq("name", wh_name))
        if not wh:
            raise bad_request("Almacén no encontrado")
        warehouse_id = wh["id"]

    # --- reclamar el paso (UPDATE condicional): solo una petición puede continuar
    claimed = execute(
        db().table("business_orders").update({"current_step": STEP_READY + 1, "warehouse_id": warehouse_id})
        .eq("id", order["id"]).eq("current_step", step).eq("is_cancelled", False)
    ).data
    if not claimed:
        raise conflict("La orden ya fue procesada por otra operación")

    # Emisión = fecha de recepción; vencimiento = emisión + días de la condición de pago de la orden (Contado: el mismo día).
    issued_at = str(payload.get("invoice_issued_at") or payload.get("received_at") or datetime.now(timezone.utc).date().isoformat())[:10]
    due_at = str(payload.get("invoice_due_at") or (date.fromisoformat(issued_at) + timedelta(days=max(terms_days, 0))).isoformat())[:10]

    done: dict[str, Any] = {"restore": []}
    try:
        receipt_number = _next_code("NI")
        condition = "OBSERVATIONS" if (differences or payload.get("observations")) else "CONFORMING"
        received_at = str(payload.get("received_at") or datetime.now(timezone.utc).date().isoformat())
        observations = "; ".join(filter(None, [str(payload.get("observations") or "").strip(), "Diferencias: " + ", ".join(differences) if differences else ""]))
        receipt = execute(db().table("goods_receipts").insert({
            "order_id": order["id"],
            "receipt_number": receipt_number,
            "received_at": received_at if "T" in received_at else f"{received_at}T12:00:00+00:00",
            "supplier_guide_reference": payload.get("supplier_guide") or None,
            "warehouse_id": warehouse_id,
            "condition": condition,
            "observations": observations or None,
            "received_by_user_id": actor.id,
        })).data[0]
        done["receipt_id"] = receipt["id"]
        done["restore"] = _post_receipt_movements(order, receipt["id"], warehouse_id, actor, received)

        invoice = execute(db().table("supplier_invoices").insert({
            "order_id": order["id"],
            "supplier_id": order["supplier_id"],
            "series_number": series_number,
            "issued_at": issued_at,
            "due_at": due_at,
            "currency": "PEN",
            "total": invoice_total,
            "validation_status": "MATCHES_ORDER_RECEIPT" if (not differences and abs(invoice_total - ordered_total) <= 0.01) else "HAS_DIFFERENCES",
            "created_by_user_id": actor.id,
        })).data[0]
        done["invoice_id"] = invoice["id"]
        if document_id:  # vincula el archivo subido con la orden y el proveedor
            execute(db().table("documents").update({
                "related_order_id": order["id"], "related_supplier_id": order["supplier_id"], "category": "INVOICE",
            }).eq("id", document_id))

        payment = None
        if pay_now:
            payment = register_supplier_payment(
                invoice, invoice_total, channel,
                {
                    "bank_account_id": payload.get("bank_account_id"),
                    "reference": payload.get("payment_reference") or order["code"],
                    "description": f"Pago {order['code']} · {order.get('party_name') or ''} · {invoice_label}",
                },
                actor, meta, audit_reference=order["code"],
            )
            done["payment_id"] = payment["id"]

        final_step = 5 if payment else 4
        _insert_event(order["id"], 2, actor, f"Ingreso {receipt_number}", {
            "fr": received_at[:10], "gr": str(payload.get("supplier_guide") or ""), "alm": wh_name or "",
            "est": "Con observaciones" if condition == "OBSERVATIONS" else "Conforme", "obs": observations,
            "lines": "; ".join(f"{ln.get('sku_snapshot')}={received[ln['id']]:g}/{float(ln['quantity']):g}" for ln in order_lines),
        })
        _insert_event(order["id"], 3, actor, invoice_label, {
            "tipo": invoice_type, "num": series_number, "fe": issued_at, "fv": due_at,
            "mon": f"{invoice_total:.2f}", "document": str(document_id or ""),
        })
        if payment:
            _insert_event(order["id"], 4, actor, f"Pago {payment.get('code')}", {
                "mon": f"{invoice_total:.2f}", "med": "Efectivo" if channel == "CASH" else "Transferencia",
                "cta": "Caja" if channel == "CASH" else "Banco", "op": str(payload.get("payment_reference") or order["code"]),
            })
        execute(db().table("business_orders").update({"current_step": final_step}).eq("id", order["id"]))
    except Exception:
        # Compensación: deshacer lo escrito (de atrás hacia adelante) y devolver la orden al paso 2.
        def _safe(fn):  # un fallo al revertir no debe ocultar el error original
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
        if done.get("payment_id"):
            _safe(lambda: delete_payment(done["payment_id"]))
        _safe(lambda: execute(db().table("order_step_events").delete().eq("order_id", order["id"]).in_("step_no", [2, 3, 4])))
        if done.get("invoice_id"):
            _safe(lambda: execute(db().table("supplier_invoices").delete().eq("id", done["invoice_id"])))
        if done.get("receipt_id"):
            _safe(lambda: execute(db().table("inventory_movements").delete().eq("goods_receipt_id", done["receipt_id"])))
            _safe(lambda: execute(db().table("goods_receipts").delete().eq("id", done["receipt_id"])))
        _safe(lambda: _restore_costs(done["restore"]))
        _safe(lambda: execute(db().table("business_orders").update({"current_step": step, "warehouse_id": order["warehouse_id"]}).eq("id", order["id"])))
        record(actor.actor, action="EDIT", module_key=MODULE_RECEIPTS, entity_type="BusinessOrder", entity_id=order["id"],
               reference=order["code"], description="Aprobación de recepción fallida; no se registró ningún cambio",
               meta=meta, result="FAILURE")
        raise

    record(actor.actor, action="EDIT", module_key=MODULE_RECEIPTS, entity_type="GoodsReceipt", entity_id=done["receipt_id"],
           reference=order["code"],
           description=f"Recepción aprobada {receipt_number} · {order['code']} · ingreso de {sum(received.values()):g} unidades"
                       + (f" · {'; '.join(differences)}" if differences else ""),
           meta=meta)
    record(actor.actor, action="CREATE", module_key=MODULE_RECEIPTS, entity_type="SupplierInvoice", entity_id=done["invoice_id"],
           reference=order["code"], description=f"{invoice_label} registrada · S/ {invoice_total:.2f} · {order['code']}",
           meta=meta)
    return get_purchase_order(order["id"])


def cancel_purchase_order(order_id_or_code: str, actor: AuthUser, meta: RequestMeta, reason: str | None = None) -> dict[str, Any]:
    order = find_by_id_or_code("business_orders", order_id_or_code, kind=KIND)
    if not order:
        raise not_found("Orden de compra no encontrada")
    if order.get("is_cancelled"):
        raise bad_request("La orden ya está anulada")
    step = int(order.get("current_step") or 1)
    if step >= MAX_STEP:
        raise bad_request("No se puede anular una orden ya pagada")
    if step >= 3:
        # Desde la recepción ya hay ingreso de stock (y luego factura y pago): anular no los revierte.
        raise bad_request("No se puede anular una orden ya recibida; el ingreso de stock, la factura y los pagos no se revierten")
    now = datetime.now(timezone.utc).isoformat()
    execute(db().table("business_orders").update({
        "is_cancelled": True, "cancelled_at": now, "cancelled_by_user_id": actor.id,
    }).eq("id", order["id"]))
    record(actor.actor, action="EDIT", module_key=MODULE, entity_type="BusinessOrder",
           entity_id=order["id"], reference=order["code"],
           description=f"Orden de compra anulada{(' · ' + reason) if reason else ''}", meta=meta)
    return get_purchase_order(order["id"])