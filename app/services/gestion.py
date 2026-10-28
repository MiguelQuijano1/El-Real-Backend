"""Módulos de Gestión: interruptores, metas, comisiones, inventario físico, hoja de ruta y flujo de caja.

Todo lo que antes guardaban esas pantallas en el navegador se guarda aquí (ver database/010_modulos_gestion.sql).
Los datos del sistema que ya existen (ventas, stock, cuentas por cobrar/pagar…) siguen leyéndose de sus propias rutas.
"""
from datetime import datetime, timezone
from typing import Any

from app.core.errors import ApiError, bad_request, conflict, not_found
from app.repositories.client import db, execute, find_by_id_or_code, first, rows
from app.schemas import gestion as sch
from app.security.deps import AuthUser
from app.services import inventory as inventory_svc
from app.services.audit import RequestMeta, record

MODULE_IDS = ("reposicion", "flujoCaja", "metas", "comisiones", "conteo", "entregas")
CHUNK = 100  # ids por consulta .in_() (evita URLs demasiado largas)
PAGE = 1000  # filas por página de PostgREST


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f(value: Any) -> float:
    return round(float(value or 0), 2)


def _one(table: str, row_id: str, label: str, select: str = "*") -> dict[str, Any]:
    row = first(db().table(table).select(select).eq("id", row_id))
    if not row:
        raise not_found(f"{label} not found")
    return row


def _all(make_query) -> list[dict[str, Any]]:
    """Lee todas las filas de una consulta paginando (PostgREST devuelve como máximo 1000 por petición)."""
    out: list[dict[str, Any]] = []
    while True:
        page = rows(make_query().range(len(out), len(out) + PAGE - 1))
        out.extend(page)
        if len(page) < PAGE:
            return out


# ═════════════════════════════ Interruptores de módulos ═════════════════════════════
def get_switches() -> dict[str, bool]:
    result = {m: True for m in MODULE_IDS}
    for r in rows(db().table("module_switches").select("module_id, enabled")):
        if r["module_id"] in result:
            result[r["module_id"]] = bool(r["enabled"])
    return result


def set_switch(module_id: str, enabled: bool, actor: AuthUser, meta: RequestMeta) -> dict[str, bool]:
    if module_id not in MODULE_IDS:
        raise not_found("Module not found")
    before = get_switches()[module_id]
    execute(db().table("module_switches").upsert(
        {"module_id": module_id, "enabled": enabled, "updated_by_user_id": actor.id}, on_conflict="module_id"))
    if before != enabled:
        record(actor.actor, action="EDIT", module_key="settings", entity_type="ModuleSwitch", reference=module_id,
               description=f"Módulo opcional «{module_id}» {'activado' if enabled else 'apagado'}",
               changes=[("enabled", str(before), str(enabled))], meta=meta)
    return get_switches()


# ═════════════════════════════ Metas ═════════════════════════════
def list_goals(period: str | None) -> list[dict[str, Any]]:
    q = db().table("sales_goals").select("id, period, kind, target, amount, created_at, updated_at")
    if period:
        q = q.eq("period", period)
    return rows(q.order("period", desc=True).order("target"))


def create_goal(dto: sch.GoalCreate, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    data = {"period": dto.period, "kind": dto.kind, "target": dto.target, "amount": _f(dto.amount), "created_by_user_id": actor.id}
    created = execute(db().table("sales_goals").insert(data)).data[0]
    record(actor.actor, action="CREATE", module_key="settings", entity_type="SalesGoal", entity_id=created["id"],
           reference=f"{dto.period} · {dto.target}", description=f"Meta creada: {dto.target} ({dto.period}) S/ {_f(dto.amount):,.2f}", meta=meta)
    return _one("sales_goals", created["id"], "Goal")


def update_goal(goal_id: str, dto: sch.GoalUpdate, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    patch = {k: (_f(v) if k == "amount" else v) for k, v in dto.model_dump(exclude_unset=True).items() if v is not None}
    if not patch:
        raise bad_request("At least one field must be provided")
    before = _one("sales_goals", goal_id, "Goal")
    execute(db().table("sales_goals").update(patch).eq("id", goal_id))
    after = _one("sales_goals", goal_id, "Goal")
    changes = [(k, str(before[k]), str(after[k])) for k in patch if str(before[k]) != str(after[k])]
    if changes:
        record(actor.actor, action="EDIT", module_key="settings", entity_type="SalesGoal", entity_id=goal_id,
               reference=f"{after['period']} · {after['target']}", description="Meta actualizada", changes=changes, meta=meta)
    return after


def delete_goal(goal_id: str, actor: AuthUser, meta: RequestMeta) -> None:
    goal = _one("sales_goals", goal_id, "Goal")
    execute(db().table("sales_goals").delete().eq("id", goal_id))
    record(actor.actor, action="DELETE", module_key="settings", entity_type="SalesGoal", entity_id=goal_id,
           reference=f"{goal['period']} · {goal['target']}", description=f"Meta eliminada: {goal['target']} ({goal['period']})", meta=meta)


def copy_goals(dto: sch.GoalCopy, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    """Copia al mes destino las metas del mes origen que aún no existen allí (mismo tipo y objetivo)."""
    if dto.from_period == dto.to_period:
        raise bad_request("Source and destination periods must differ")
    source = list_goals(dto.from_period)
    existing = {(g["kind"], g["target"]) for g in list_goals(dto.to_period)}
    fresh = [
        {"period": dto.to_period, "kind": g["kind"], "target": g["target"], "amount": g["amount"], "created_by_user_id": actor.id}
        for g in source if (g["kind"], g["target"]) not in existing
    ]
    if fresh:
        execute(db().table("sales_goals").insert(fresh))
        record(actor.actor, action="CREATE", module_key="settings", entity_type="SalesGoal", reference=dto.to_period,
               description=f"{len(fresh)} meta(s) copiadas de {dto.from_period} a {dto.to_period}", meta=meta)
    return {"created": len(fresh), "items": list_goals(dto.to_period)}


# ═════════════════════════════ Comisiones ═════════════════════════════
def get_commissions() -> dict[str, Any]:
    setting = first(db().table("commission_settings").select("default_pct").eq("id", 1))
    rates = rows(db().table("commission_rates").select("seller, pct"))
    payouts = rows(db().table("commission_payouts").select("seller, period, amount, paid_at, paid_by_name").order("paid_at", desc=True).limit(1000))
    return {
        "default_pct": float(setting["default_pct"]) if setting else 2.0,
        "by_seller": {r["seller"]: float(r["pct"]) for r in rates},
        "payouts": payouts,
    }


def set_default_pct(dto: sch.DefaultPctIn, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    before = get_commissions()["default_pct"]
    execute(db().table("commission_settings").upsert({"id": 1, "default_pct": _f(dto.pct)}, on_conflict="id"))
    if before != _f(dto.pct):
        record(actor.actor, action="EDIT", module_key="settings", entity_type="CommissionRule", reference="general",
               description="% general de comisión actualizado", changes=[("default_pct", str(before), str(_f(dto.pct)))], meta=meta)
    return get_commissions()


def set_seller_pct(dto: sch.SellerPctIn, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    before = get_commissions()["by_seller"].get(dto.seller)
    if dto.pct is None:
        execute(db().table("commission_rates").delete().eq("seller", dto.seller))
    else:
        execute(db().table("commission_rates").upsert({"seller": dto.seller, "pct": _f(dto.pct)}, on_conflict="seller"))
    after = None if dto.pct is None else _f(dto.pct)
    if before != after:
        record(actor.actor, action="EDIT", module_key="settings", entity_type="CommissionRule", reference=dto.seller,
               description=f"% de comisión de {dto.seller} actualizado", changes=[("pct", str(before), str(after))], meta=meta)
    return get_commissions()


def create_payout(dto: sch.PayoutIn, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    """Registra (o reemplaza) la liquidación pagada de un vendedor en un mes. El egreso se registra aparte en Caja/Bancos."""
    execute(db().table("commission_payouts").upsert(
        {"seller": dto.seller, "period": dto.period, "amount": _f(dto.amount), "paid_at": _now(),
         "paid_by_user_id": actor.id, "paid_by_name": actor.full_name},
        on_conflict="seller,period"))
    record(actor.actor, action="CREATE", module_key="settings", entity_type="CommissionPayout", reference=f"{dto.seller} · {dto.period}",
           description=f"Comisión de {dto.seller} ({dto.period}) marcada como pagada: S/ {_f(dto.amount):,.2f}", meta=meta)
    return get_commissions()


# ═════════════════════════════ Inventario físico ═════════════════════════════
def _count_view(row: dict[str, Any], summary: dict[str, Any] | None) -> dict[str, Any]:
    s = summary or {}
    missing, surplus = float(s.get("missing_value") or 0), float(s.get("surplus_value") or 0)
    return {
        "id": row["id"], "code": row["code"], "status": row["status"],
        "warehouse": (row.get("warehouse") or {}).get("name") or "",
        "opened_by": row["opened_by_name"], "opened_at": row["opened_at"], "closed_at": row["closed_at"],
        "summary": {
            "item_count": int(s.get("item_count") or 0), "pending": int(s.get("pending") or 0), "with_diff": int(s.get("with_diff") or 0),
            "missing_value": round(missing, 2), "surplus_value": round(surplus, 2), "net": round(surplus - missing, 2),
        },
    }


_COUNT_SELECT = "id, code, status, opened_by_name, opened_at, closed_at, warehouse:warehouses(id, name)"


def list_counts(limit: int = 200) -> list[dict[str, Any]]:
    counts = rows(db().table("stock_counts").select(_COUNT_SELECT).order("opened_at", desc=True).limit(limit))
    summaries: dict[str, dict[str, Any]] = {}
    ids = [c["id"] for c in counts]
    for i in range(0, len(ids), CHUNK):
        for s in rows(db().table("stock_count_summary").select("*").in_("count_id", ids[i:i + CHUNK])):
            summaries[s["count_id"]] = s
    return [_count_view(c, summaries.get(c["id"])) for c in counts]


def get_count(count_id: str) -> dict[str, Any]:
    row = _one("stock_counts", count_id, "Count", _COUNT_SELECT)
    summary = first(db().table("stock_count_summary").select("*").eq("count_id", count_id))
    items = _all(lambda: db().table("stock_count_items")
                 .select("id, sku, name, unit, system_qty, counted_qty, unit_cost").eq("count_id", count_id).order("name").order("id"))
    return {**_count_view(row, summary), "items": items}


def _next_count_code() -> str:
    year = datetime.now(timezone.utc).year
    n = execute(db().rpc("next_sequence", {"p_type": "CF", "p_year": year, "p_series": ""})).data
    n = n[0] if isinstance(n, list) else n
    return f"CF-{year}-{int(n):04d}"


def create_count(dto: sch.CountCreate, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    """Abre un conteo con la «foto» del stock del almacén en este momento."""
    warehouse = first(db().table("warehouses").select("id, name, status").eq("name", dto.warehouse_name))
    if not warehouse or warehouse["status"] == "INACTIVE":
        raise not_found("Warehouse not found")
    stock = inventory_svc.list_stock(warehouse_id=warehouse["id"], limit=1_000_000)["items"]
    if not stock:
        raise ApiError(400, "Warehouse has no stock records", "EMPTY_WAREHOUSE")

    product_ids = [s["productId"] for s in stock]
    products: dict[str, dict[str, Any]] = {}
    for i in range(0, len(product_ids), CHUNK):
        for p in rows(db().table("products").select("id, unit, average_cost").in_("id", product_ids[i:i + CHUNK])):
            products[p["id"]] = p

    code = _next_count_code()
    created = execute(db().table("stock_counts").insert(
        {"code": code, "warehouse_id": warehouse["id"], "opened_by_user_id": actor.id, "opened_by_name": actor.full_name})).data[0]
    items = [
        {"count_id": created["id"], "sku": s["sku"], "name": s["prod"],
         "unit": (products.get(s["productId"]) or {}).get("unit") or "und.",
         "system_qty": round(float(s["stock"]), 3), "unit_cost": float((products.get(s["productId"]) or {}).get("average_cost") or 0)}
        for s in stock
    ]
    try:
        for i in range(0, len(items), 500):
            execute(db().table("stock_count_items").insert(items[i:i + 500]))
    except Exception:
        execute(db().table("stock_counts").delete().eq("id", created["id"]))  # no deja un conteo a medias
        raise
    record(actor.actor, action="CREATE", module_key="adjustments", entity_type="StockCount", entity_id=created["id"], reference=code,
           description=f"Conteo físico {code} abierto en {warehouse['name']} ({len(items)} productos)", meta=meta)
    return get_count(created["id"])


def _require_open(count_id: str) -> dict[str, Any]:
    row = _one("stock_counts", count_id, "Count", "id, code, status")
    if row["status"] != "OPEN":
        raise ApiError(409, "Count is closed", "COUNT_NOT_OPEN")
    return row


def set_counted(count_id: str, item_id: str, dto: sch.CountedIn) -> dict[str, Any]:
    _require_open(count_id)
    counted = None if dto.counted is None else round(float(dto.counted), 3)
    updated = execute(db().table("stock_count_items").update({"counted_qty": counted}).eq("id", item_id).eq("count_id", count_id)).data
    if not updated:
        raise not_found("Count item not found")
    return updated[0]


def close_count(count_id: str, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    row = _require_open(count_id)
    summary = first(db().table("stock_count_summary").select("pending, with_diff").eq("count_id", count_id)) or {}
    if int(summary.get("pending") or 0) > 0:
        raise ApiError(409, "There are products still to count", "PENDING_ITEMS")
    done = execute(db().table("stock_counts").update({"status": "CLOSED", "closed_at": _now()}).eq("id", count_id).eq("status", "OPEN")).data
    if not done:
        raise ApiError(409, "Count is closed", "COUNT_NOT_OPEN")
    record(actor.actor, action="EDIT", module_key="adjustments", entity_type="StockCount", entity_id=count_id, reference=row["code"],
           description=f"Conteo físico {row['code']} cerrado con {int(summary.get('with_diff') or 0)} diferencia(s)", meta=meta)
    return get_count(count_id)


# ═════════════════════════════ Hoja de ruta (entregas) ═════════════════════════════
def _delivery_view(code: str, r: dict[str, Any]) -> dict[str, Any]:
    return {"order_code": code, "status": r["status"], "receiver": r["receiver"], "reason": r["reason"], "note": r["note"],
            "confirmed_at": r["confirmed_at"], "confirmed_by": r["confirmed_by_name"]}


def list_deliveries(codes: list[str]) -> list[dict[str, Any]]:
    """Confirmaciones de las órdenes de venta indicadas (la pantalla pide solo las del día que muestra)."""
    if not codes:
        return []
    orders: dict[str, str] = {}
    for i in range(0, len(codes), CHUNK):
        for o in rows(db().table("business_orders").select("id, code").eq("kind", "SALE").in_("code", codes[i:i + CHUNK])):
            orders[o["id"]] = o["code"]
    out: list[dict[str, Any]] = []
    ids = list(orders)
    for i in range(0, len(ids), CHUNK):
        for r in rows(db().table("delivery_confirmations").select("*").in_("order_id", ids[i:i + CHUNK])):
            out.append(_delivery_view(orders[r["order_id"]], r))
    return out


def _dispatched_order(code: str) -> dict[str, Any]:
    order = find_by_id_or_code("business_orders", code, "id, code, is_cancelled, current_step", kind="SALE")
    if not order:
        raise not_found("Sales order not found")
    if order["is_cancelled"] or int(order["current_step"] or 1) < 3:
        raise ApiError(409, "The order has not been dispatched", "ORDER_NOT_DISPATCHED")
    return order


def upsert_delivery(code: str, dto: sch.DeliveryIn, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    order = _dispatched_order(code)
    receiver = (dto.receiver or "").strip()
    reason = (dto.reason or "").strip()
    if dto.status == "DELIVERED" and not receiver:
        raise bad_request("receiver is required when the order was delivered")
    if dto.status == "NOT_DELIVERED" and not reason:
        raise bad_request("reason is required when the order was not delivered")
    data = {
        "order_id": order["id"], "status": dto.status,
        "receiver": receiver or None if dto.status == "DELIVERED" else None,
        "reason": reason or None if dto.status == "NOT_DELIVERED" else None,
        "note": (dto.note or "").strip() or None,
        "confirmed_at": _now(), "confirmed_by_user_id": actor.id, "confirmed_by_name": actor.full_name,
    }
    saved = execute(db().table("delivery_confirmations").upsert(data, on_conflict="order_id")).data[0]
    label = "entregada" if dto.status == "DELIVERED" else "no entregada"
    record(actor.actor, action="EDIT", module_key="sales-orders", entity_type="DeliveryConfirmation", entity_id=order["id"],
           reference=order["code"], description=f"Entrega de {order['code']} marcada como {label}", meta=meta)
    return _delivery_view(order["code"], saved)


def delete_delivery(code: str, actor: AuthUser, meta: RequestMeta) -> None:
    order = find_by_id_or_code("business_orders", code, "id, code", kind="SALE")
    if not order:
        raise not_found("Sales order not found")
    execute(db().table("delivery_confirmations").delete().eq("order_id", order["id"]))
    record(actor.actor, action="EDIT", module_key="sales-orders", entity_type="DeliveryConfirmation", entity_id=order["id"],
           reference=order["code"], description=f"Entrega de {order['code']} vuelta a pendiente", meta=meta)


# ═════════════════════════════ Flujo de caja ═════════════════════════════
def get_cashflow() -> dict[str, Any]:
    row = first(db().table("cashflow_settings").select("opening_balance").eq("id", 1))
    value = row["opening_balance"] if row else None
    return {"opening_balance": None if value is None else float(value)}


def set_cashflow(dto: sch.CashflowIn, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    before = get_cashflow()["opening_balance"]
    after = None if dto.opening_balance is None else _f(dto.opening_balance)
    execute(db().table("cashflow_settings").upsert({"id": 1, "opening_balance": after}, on_conflict="id"))
    if before != after:
        record(actor.actor, action="EDIT", module_key="cash", entity_type="CashflowSetting", reference="saldo inicial",
               description="Saldo inicial del flujo de caja actualizado", changes=[("opening_balance", str(before), str(after))], meta=meta)
    return get_cashflow()