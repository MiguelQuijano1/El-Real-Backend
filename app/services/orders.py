from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.core.errors import bad_request, not_found
from app.repositories.client import db, execute, find_by_id_or_code, first, rows
from app.services.costing import get_average_cost
from app.security.deps import AuthUser
from app.services.audit import Actor, RequestMeta, record

MODULE = "sales-orders"
KIND_SALE = "SALE"
MAX_STEP = 5  # 1=creada … 5=cobrada

_LIMA = timezone(timedelta(hours=-5))


def is_past_deadline(limit: Any, today: date | None = None) -> bool:
    """La fecha límite de pago sigue vigente durante todo ese día; vence al día siguiente."""
    if not limit:
        return False
    try:
        d = date.fromisoformat(str(limit)[:10])
    except ValueError:
        return False
    return d < (today or datetime.now(_LIMA).date())


def _next_code(prefix: str, year: int | None = None) -> str:
    y = year or datetime.now(timezone.utc).year
    n = execute(db().rpc("next_sequence", {"p_type": prefix, "p_year": y, "p_series": ""})).data
    n = n[0] if isinstance(n, list) else n
    return f"{prefix}-{y}-{int(n):04d}"


def _terms_label(days: int) -> str:
    if days <= 0:
        return "Contado"
    return f"Crédito {days} días"


def _days_from_term(label: str | None) -> int:
    if not label:
        return 0
    import re
    m = re.search(r"(\d+)", str(label))
    return int(m.group(1)) if m else 0


def _embed() -> str:
    # Sin nombre de FK explícito: PostgREST resuelve por columna cuando no hay ambigüedad.
    return (
        "*, "
        "lines:order_lines(*), "
        "events:order_step_events(*), "
        "warehouse:warehouses(id, code, name), "
        "customer:customers(id, code, legal_name, document_number, contact_name, phone, fiscal_address, billing_email, payment_terms_days)"
    )


def _serialize(row: dict[str, Any], seller_name: str | None = None) -> dict[str, Any]:
    """Normaliza la fila de Supabase al contrato que consume el frontend."""
    lines = sorted(row.get("lines") or [], key=lambda x: x.get("line_no", 0))
    events = sorted(row.get("events") or [], key=lambda x: x.get("step_no", 0))
    customer = row.get("customer") or {}
    warehouse = row.get("warehouse") or {}
    seller = {"full_name": seller_name or ""}

    forms: dict[str, dict[str, Any]] = {}
    docs: dict[str, str] = {}
    log: dict[str, dict[str, Any]] = {}
    for ev in events:
        step = int(ev.get("step_no") or 0)
        key = str(step)
        # form_data puede existir en BD desplegada; si no, note como JSON no es fiable
        fd = ev.get("form_data")
        if isinstance(fd, dict):
            forms[key] = {str(k): str(v) if v is not None else "" for k, v in fd.items()}
        elif isinstance(ev.get("note"), str) and ev["note"].startswith("{"):
            import json
            try:
                forms[key] = {str(k): str(v) for k, v in json.loads(ev["note"]).items()}
            except Exception:
                forms[key] = {}
        else:
            forms[key] = {}
        docs[key] = str(ev.get("generated_reference") or "")
        log[key] = {
            "who": "",  # se completa abajo si hace falta
            "at": ev.get("completed_at"),
            "userId": ev.get("completed_by_user_id"),
        }

    terms_days = int(row.get("payment_terms_days") or 0)
    party = {
        "code": customer.get("code") or "",
        "name": row.get("party_name") or customer.get("legal_name") or "",
        "ruc": row.get("party_tax_id") or customer.get("document_number") or "",
        "contact": row.get("party_contact") or customer.get("contact_name") or "",
        "terms": _terms_label(terms_days),
        "address": row.get("party_address") or customer.get("fiscal_address") or "",
        "email": row.get("party_email") or customer.get("billing_email") or "",
    }

    return {
        "id": row["id"],
        "code": row["code"],
        "kind": "venta" if row.get("kind") == KIND_SALE else "compra",
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
        "customerId": row.get("customer_id"),
        "warehouseId": row.get("warehouse_id"),
        "warehouse": warehouse.get("name") or "",
        "sellerUserId": row.get("seller_user_id"),
        "seller": seller.get("full_name") or "",
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


def credit_problem(limit: float, used: float, total: float) -> str | None:
    """Motivo por el que una venta a crédito no cabe en la línea del cliente (None si cabe)."""
    if limit <= 0:
        return "El cliente no tiene línea de crédito: la venta debe ser al contado"
    available = round(limit - used, 2)
    if total - available > 0.005:
        return (
            f"La venta (S/ {total:,.2f}) supera el crédito disponible del cliente "
            f"(S/ {max(available, 0):,.2f} de una línea de S/ {limit:,.2f})"
        )
    return None


def customer_credit_used(customer_id: str) -> float:
    """Crédito en uso: saldo por cobrar de sus facturas + ventas a crédito aún sin facturar."""
    invoices = rows(
        db()
        .table("sales_invoices")
        .select("id, total")
        .eq("customer_id", customer_id)
        .neq("tax_status", "VOIDED")
        .neq("document_type", "CREDIT_NOTE")
    )
    ids = [i["id"] for i in invoices]
    paid = 0.0
    if ids:
        for m in rows(
            db()
            .table("treasury_movements")
            .select("signed_amount")
            .eq("movement_type", "COLLECTION")
            .in_("sales_invoice_id", ids)
        ):
            paid += abs(float(m.get("signed_amount") or 0))
    owed = max(0.0, sum(float(i.get("total") or 0) for i in invoices) - paid)

    open_orders = rows(
        db()
        .table("business_orders")
        .select("id, tax_rate, payment_terms_days")
        .eq("kind", KIND_SALE)
        .eq("customer_id", customer_id)
        .eq("is_cancelled", False)
        .lte("current_step", 3)
    )
    pending = 0.0
    for o in open_orders:
        if int(o.get("payment_terms_days") or 0) <= 0:
            continue
        lines = rows(db().table("order_lines").select("quantity, unit_price").eq("order_id", o["id"]))
        sub = sum(float(ln.get("quantity") or 0) * float(ln.get("unit_price") or 0) for ln in lines)
        pending += sub * (1 + float(o.get("tax_rate") or 0.18))
    return round(owed + pending, 2)


def _pay_deadlines(order_ids: list[str]) -> dict[str, str]:
    """Fecha límite de pago (`lim`) guardada en el evento de creación de cada orden."""
    if not order_ids:
        return {}
    events = execute(
        db().table("order_step_events").select("order_id, step_no, form_data").in_("order_id", order_ids)
    ).data or []
    out: dict[str, str] = {}
    for e in events:
        lim = (e.get("form_data") or {}).get("lim")
        if lim:
            out[e["order_id"]] = str(lim)
    return out


def expire_unpaid_orders(today: date | None = None) -> int:
    """Anula las ventas al contado que siguen sin aprobar y pasaron su fecha límite de pago.

    Aún no salió mercadería (el despacho ocurre al aprobar), así que anular no revierte nada. Se ejecuta al
    listar órdenes y al intentar aprobar una; no hace falta ningún proceso programado.
    """
    open_orders = (
        execute(
            db()
            .table("business_orders")
            .select("id, code")
            .eq("kind", KIND_SALE)
            .eq("is_cancelled", False)
            .lte("current_step", 1)
        ).data
        or []
    )
    if not open_orders:
        return 0
    deadlines = _pay_deadlines([o["id"] for o in open_orders])
    expired = [o for o in open_orders if is_past_deadline(deadlines.get(o["id"]), today)]
    now = datetime.now(timezone.utc).isoformat()
    for o in expired:
        execute(db().table("business_orders").update({"is_cancelled": True, "cancelled_at": now}).eq("id", o["id"]))
        record(
            Actor(user_name="Sistema"),
            action="EDIT",
            module_key=MODULE,
            entity_type="BusinessOrder",
            entity_id=o["id"],
            reference=o["code"],
            description=f"Orden de venta anulada por falta de pago · venció el {deadlines.get(o['id'])}",
        )
    return len(expired)


def list_sale_orders(
    *,
    q: str | None = None,
    cancelled: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    try:
        expire_unpaid_orders()
    except Exception:  # la lista no debe fallar si el barrido no pudo correr
        pass
    query = (
        db()
        .table("business_orders")
        .select(_embed(), count="exact")
        .eq("kind", KIND_SALE)
    )
    if cancelled is True:
        query = query.eq("is_cancelled", True)
    elif cancelled is False:
        query = query.eq("is_cancelled", False)
    if q:
        term = q.strip().replace(",", " ")
        if term:
            query = query.or_(
                f"code.ilike.%{term}%,party_name.ilike.%{term}%,party_tax_id.ilike.%{term}%"
            )
    res = execute(query.order("created_at", desc=True).range(offset, offset + limit - 1))
    rows = res.data or []
    seller_ids = {r.get("seller_user_id") for r in rows if r.get("seller_user_id")}
    names: dict[str, str] = {}
    if seller_ids:
        for u in execute(db().table("users").select("id, full_name").in_("id", list(seller_ids))).data or []:
            names[u["id"]] = u.get("full_name") or ""
    items = [_serialize(r, names.get(r.get("seller_user_id") or "")) for r in rows]
    return {"items": items, "total": res.count or 0, "limit": limit, "offset": offset}


def get_sale_order(order_id_or_code: str) -> dict[str, Any]:
    row = find_by_id_or_code("business_orders", order_id_or_code, _embed(), kind=KIND_SALE)
    if not row:
        raise not_found("Orden de venta no encontrada")
    seller_name = ""
    if row.get("seller_user_id"):
        u = first(db().table("users").select("full_name").eq("id", row["seller_user_id"]))
        seller_name = (u or {}).get("full_name") or ""
    return _serialize(row, seller_name)


def discount_error(net_subtotal: float, discount: float, max_pct: float) -> str | None:
    """El descuento global (sobre el subtotal antes del descuento) no puede pasar el máximo de Configuración."""
    gross = net_subtotal + discount
    if net_subtotal <= 0:
        return "El descuento no puede ser igual o mayor que el subtotal de la orden"
    pct = discount / gross * 100
    if pct > max_pct + 0.01:
        return f"El descuento ({pct:.1f}%) supera el máximo permitido en Configuración ({max_pct:g}%)"
    return None


def create_sale_order(payload: dict[str, Any], actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    customer_id = payload["customer_id"]
    warehouse_id = payload["warehouse_id"]
    lines_in = payload.get("lines") or []
    if not lines_in:
        raise bad_request("La orden debe tener al menos una línea")

    customer = first(db().table("customers").select("*").eq("id", customer_id))
    if not customer:
        raise bad_request("Cliente no encontrado")
    warehouse = first(db().table("warehouses").select("id, name, status").eq("id", warehouse_id))
    if not warehouse:
        raise bad_request("Almacén no encontrado")
    if warehouse.get("status") == "INACTIVE":
        raise bad_request("El almacén está inactivo")

    seller_id = payload.get("seller_user_id") or actor.id
    terms_days = int(payload.get("payment_terms_days") if payload.get("payment_terms_days") is not None else customer.get("payment_terms_days") or 0)
    tax_rate = payload.get("tax_rate")
    if tax_rate is None:
        settings = first(db().table("company_settings").select("tax_rate").eq("id", 1))
        tax_rate = (settings or {}).get("tax_rate", 0.18)

    # Venta a crédito: debe caber en la línea de crédito del cliente (saldo por cobrar + ventas a crédito sin facturar).
    if terms_days > 0:
        order_total = sum(float(ln["quantity"]) * float(ln["unit_price"]) for ln in lines_in) * (1 + float(tax_rate))
        problem = credit_problem(float(customer.get("credit_limit") or 0), customer_credit_used(customer_id), order_total)
        if problem:
            raise bad_request(problem)

    party_name = payload.get("party_name") or customer.get("legal_name")
    party_tax_id = payload.get("party_tax_id") or customer.get("document_number")
    discount = float(payload.get("discount_amount") or 0)
    if discount > 0:
        net = sum(float(ln["quantity"]) * float(ln["unit_price"]) for ln in lines_in)
        settings_row = first(db().table("company_settings").select("max_discount_pct").eq("id", 1)) or {}
        err = discount_error(net, discount, float(settings_row.get("max_discount_pct") or 0))
        if err:
            raise bad_request(err)
    if payload.get("document_type") == "Factura" and len("".join(c for c in str(party_tax_id or "") if c.isdigit())) != 11:
        raise bad_request("Solo se puede emitir factura a un cliente con RUC; usa boleta.")
    party_address = payload.get("delivery_address") or payload.get("party_address") or customer.get("fiscal_address")
    party_contact = payload.get("party_contact") or customer.get("contact_name")
    party_email = payload.get("party_email") or customer.get("billing_email")

    code = _next_code("OV")
    order_row = {
        "code": code,
        "kind": KIND_SALE,
        "customer_id": customer_id,
        "warehouse_id": warehouse_id,
        "seller_user_id": seller_id,
        "created_by_user_id": actor.id,
        "party_name": party_name,
        "party_tax_id": party_tax_id,
        "party_address": party_address,
        "party_contact": party_contact,
        "party_email": party_email,
        "payment_terms_days": terms_days,
        "tax_rate": float(tax_rate),
        "current_step": 1,
        "is_cancelled": False,
    }
    created = execute(db().table("business_orders").insert(order_row)).data[0]
    order_id = created["id"]

    line_rows = []
    for i, ln in enumerate(lines_in, start=1):
        line_rows.append(
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
        )
    execute(db().table("order_lines").insert(line_rows))

    # Paso 0/1: creación (venta directa)
    origin = payload.get("origin") or "Venta directa"
    now = datetime.now(timezone.utc).isoformat()
    event = {
        "order_id": order_id,
        "step_no": 0,
        "completed_by_user_id": actor.id,
        "completed_at": now,
        "generated_reference": origin,
        "note": origin,
    }
    # Datos de entrega elegidos en el modal de venta (transportista, fecha y dirección de entrega).
    # `lim`: fecha límite de pago de una venta al contado; si pasa sin aprobarse, la orden se anula sola.
    creation_form = {
        k: str(v)
        for k, v in (
            ("lim", payload.get("pay_deadline")),
            ("tra", payload.get("carrier")),
            ("con", payload.get("driver")),
            ("pla", payload.get("vehicle_plate")),
            ("ent", payload.get("delivery_date")),
            ("dir", payload.get("delivery_address")),
            # `tdc`: comprobante elegido al crear (Factura/Boleta); precarga el paso de facturación.
            ("tdc", payload.get("document_type")),
            # `dsc`: descuento global en soles (ya repartido en los precios de las líneas).
            ("dsc", f"{float(payload.get('discount_amount') or 0):.2f}" if float(payload.get("discount_amount") or 0) > 0 else None),
        )
        if v
    }
    # form_data si la columna existe en la BD del usuario
    try:
        execute(db().table("order_step_events").insert({**event, "form_data": creation_form}))
    except Exception:
        execute(db().table("order_step_events").insert(event))

    record(
        actor.actor,
        action="CREATE",
        module_key=MODULE,
        entity_type="BusinessOrder",
        entity_id=order_id,
        reference=code,
        description=f"Orden de venta creada · {party_name}",
        meta=meta,
    )
    return get_sale_order(order_id)


def complete_step(
    order_id_or_code: str,
    payload: dict[str, Any],
    actor: AuthUser,
    meta: RequestMeta,
) -> dict[str, Any]:
    order = find_by_id_or_code("business_orders", order_id_or_code, kind=KIND_SALE)
    if not order:
        raise not_found("Orden de venta no encontrada")
    if order.get("is_cancelled"):
        raise bad_request("La orden está anulada")
    step = int(order.get("current_step") or 1)
    if step >= MAX_STEP:
        raise bad_request("La orden ya completó todos los pasos")

    if step <= 1 and is_past_deadline(_pay_deadlines([order["id"]]).get(order["id"])):
        expire_unpaid_orders()
        raise bad_request("La orden venció por falta de pago y fue anulada. Crea una nueva orden")

    values = payload.get("values") or {}
    # Normalizar a strings para el frontend
    values_str = {str(k): "" if v is None else str(v) for k, v in values.items()}
    generated = payload.get("generated_reference")
    note = payload.get("note")

    # Efectos por paso (lado servidor)
    patch: dict[str, Any] = {"current_step": step + 1}
    if step == 1:
        # Confirmar orden: almacén y condición de pago
        if values_str.get("alm"):
            wh = first(db().table("warehouses").select("id, name").eq("name", values_str["alm"]))
            if wh:
                patch["warehouse_id"] = wh["id"]
        if values_str.get("cond"):
            patch["payment_terms_days"] = _days_from_term(values_str["cond"])
        if values_str.get("dir"):
            patch["party_address"] = values_str["dir"]
        generated = generated or order["code"]
    elif step == 2:
        # Despacho: crear registro en dispatches si es posible
        generated = generated or _create_dispatch(order, values_str, actor)
    elif step == 3:
        # Factura
        generated = generated or _create_sales_invoice(order, values_str, actor)
    elif step == 4:
        # Cobro
        generated = generated or f"Cobro-{order['code']}"

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

    record(
        actor.actor,
        action="EDIT",
        module_key=MODULE,
        entity_type="BusinessOrder",
        entity_id=order["id"],
        reference=order["code"],
        description=f"Paso {step} completado · {generated or order['code']}",
        meta=meta,
        changes=[("current_step", str(step), str(step + 1))],
    )
    return get_sale_order(order["id"])



def _post_dispatch_movements(order: dict[str, Any], dispatch_id: str | None, actor: AuthUser) -> None:
    """Salida de stock al despachar venta."""
    lines = execute(db().table("order_lines").select("*").eq("order_id", order["id"]).order("line_no")).data or []
    now = datetime.now(timezone.utc).isoformat()
    for ln in lines:
        if not ln.get("product_id"):
            continue  # línea sin producto del catálogo: no mueve stock
        execute(db().table("inventory_movements").insert({
            "occurred_at": now,
            "product_id": ln["product_id"],
            "warehouse_id": order["warehouse_id"],
            "movement_type": "DISPATCH",
            "quantity_delta": -float(ln["quantity"]),
            "unit_cost": get_average_cost(ln["product_id"]),  # la salida se valoriza al costo promedio, no al precio de venta
            "order_line_id": ln["id"],
            "dispatch_id": dispatch_id,
            "reference_snapshot": order["code"],
            "created_by_user_id": actor.id,
        }))


def _ensure_stock_for_dispatch(order: dict[str, Any]) -> None:
    from app.services.inventory import _balance  # import local: inventory también usa este módulo de servicios

    settings = first(db().table("company_settings").select("allow_negative_stock").eq("id", 1)) or {}
    if settings.get("allow_negative_stock"):
        return  # la empresa permite despachar sin stock suficiente (Configuración › Inventario)
    lines = execute(db().table("order_lines").select("*").eq("order_id", order["id"]).order("line_no")).data or []
    for ln in lines:
        if not ln.get("product_id"):
            continue
        have = _balance(ln["product_id"], order["warehouse_id"])
        if have < float(ln["quantity"]):
            raise bad_request(f"Stock insuficiente de {ln.get('sku_snapshot')} en el almacén de despacho (hay {have:g}, pide {float(ln['quantity']):g})")


def _create_dispatch(order: dict[str, Any], values: dict[str, str], actor: AuthUser) -> str:
    _ensure_stock_for_dispatch(order)
    dispatch_number = _next_code("GR")
    reason = "SALE"
    if values.get("mot") and "confirmación" in values["mot"].lower():
        reason = "SALE_CONFIRMATION_PENDING"
    vehicle_id = None
    driver_id = None
    carrier_id = None
    if values.get("pla"):
        veh = first(db().table("vehicles").select("id, default_driver_id").eq("plate", values["pla"]))
        if veh:
            vehicle_id = veh["id"]
            driver_id = veh.get("default_driver_id")
    if values.get("con") and not driver_id:
        drv = first(db().table("drivers").select("id").eq("full_name", values["con"]))
        if drv:
            driver_id = drv["id"]
    if values.get("tra") and values["tra"] != "Movilidad propia":
        sup = first(db().table("suppliers").select("id").eq("legal_name", values["tra"]))
        if sup:
            carrier_id = sup["id"]

    dispatched_at = values.get("ft") or datetime.now(timezone.utc).date().isoformat()
    row = {
        "order_id": order["id"],
        "dispatch_number": dispatch_number,
        "dispatched_at": dispatched_at if "T" in dispatched_at else f"{dispatched_at}T12:00:00+00:00",
        "reason": reason,
        "carrier_supplier_id": carrier_id,
        "vehicle_id": vehicle_id,
        "driver_id": driver_id,
        "warehouse_id": order["warehouse_id"],
        "destination_snapshot": values.get("lle") or order.get("party_address") or "",
        "created_by_user_id": actor.id,
    }
    dispatch_id = execute(db().table("dispatches").insert(row)).data[0]["id"]
    try:
        _post_dispatch_movements(order, dispatch_id, actor)
    except Exception:
        # No dejar una guía ni salidas a medias: el paso no avanza y se puede reintentar.
        execute(db().table("inventory_movements").delete().eq("dispatch_id", dispatch_id))
        execute(db().table("dispatches").delete().eq("id", dispatch_id))
        raise
    return f"Guía {dispatch_number}"


def _create_sales_invoice(order: dict[str, Any], values: dict[str, str], actor: AuthUser) -> str:
    lines = execute(
        db().table("order_lines").select("*").eq("order_id", order["id"]).order("line_no")
    ).data or []
    subtotal = sum(float(ln["quantity"]) * float(ln["unit_price"]) for ln in lines)
    tax_rate = float(order.get("tax_rate") or 0.18)
    tax_amount = round(subtotal * tax_rate, 2)
    total = round(subtotal + tax_amount, 2)

    settings = first(db().table("company_settings").select("invoice_series, receipt_series, auto_tax_submission").eq("id", 1)) or {}
    # El frontend envía `tipo` ("Boleta de venta electrónica"…) y `ser`; se aceptan también `td`/`serie`.
    doc_type = "INVOICE"
    chosen_series = values.get("ser") or values.get("serie")
    series = chosen_series or settings.get("invoice_series") or "F001"
    if values.get("td") == "Boleta" or (values.get("tipo") or "").lower().startswith("boleta"):
        doc_type = "RECEIPT"
        series = chosen_series or settings.get("receipt_series") or "B001"
    number = values.get("num") or str(int(datetime.now(timezone.utc).timestamp()) % 100000).zfill(5)
    issued = values.get("fe") or datetime.now(timezone(timedelta(hours=-5))).date().isoformat()
    due = values.get("fv") or issued

    row = {
        "order_id": order["id"],
        "customer_id": order["customer_id"],
        "document_type": doc_type,
        "series": series,
        "number": number,
        "issued_at": issued,
        "due_at": due,
        "currency": "PEN",
        "subtotal": subtotal,
        "tax_amount": tax_amount,
        "total": total,
        # Con "Envío a SUNAT automático" (Configuración › Comprobantes) el comprobante nace aceptado; si no, queda
        # pendiente hasta que alguien use "Reenviar a SUNAT". (No hay conexión real con SUNAT: es el mismo estado simulado.)
        "tax_status": "ACCEPTED" if settings.get("auto_tax_submission") else "PENDING",
    }
    # Sin try/except: si la factura no se guarda, el paso no debe avanzar.
    execute(db().table("sales_invoices").insert(row))
    return f"{series}-{number}"


def cancel_sale_order(
    order_id_or_code: str,
    actor: AuthUser,
    meta: RequestMeta,
    reason: str | None = None,
) -> dict[str, Any]:
    order = find_by_id_or_code("business_orders", order_id_or_code, kind=KIND_SALE)
    if not order:
        raise not_found("Orden de venta no encontrada")
    if order.get("is_cancelled"):
        raise bad_request("La orden ya está anulada")
    step = int(order.get("current_step") or 1)
    if step >= MAX_STEP:
        raise bad_request("No se puede anular una orden ya cobrada")
    if step >= 3:
        # Desde el despacho ya hay salida de stock (y luego factura y cobros): anular no los revierte.
        raise bad_request("No se puede anular una orden ya despachada; la salida de stock, la factura y los cobros no se revierten")

    now = datetime.now(timezone.utc).isoformat()
    execute(
        db()
        .table("business_orders")
        .update(
            {
                "is_cancelled": True,
                "cancelled_at": now,
                "cancelled_by_user_id": actor.id,
            }
        )
        .eq("id", order["id"])
    )
    record(
        actor.actor,
        action="EDIT",
        module_key=MODULE,
        entity_type="BusinessOrder",
        entity_id=order["id"],
        reference=order["code"],
        description=f"Orden de venta anulada{(' · ' + reason) if reason else ''}",
        meta=meta,
    )
    return get_sale_order(order["id"])