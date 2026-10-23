"""Stock inicial desde el importador general de Mantenimiento.

Cada fila se registra igual que un ajuste de inventario de tipo «Sobrante» (motivo «Stock inicial»):
un registro en `inventory_adjustments` y su movimiento `ADJUSTMENT` en `inventory_movements`, con el costo
unitario del archivo. El costo promedio del producto se recalcula igual que en una recepción de compra.

- No duplica: si el producto ya tiene movimientos en ese almacén, la fila se OMITE (action «skip»).
- Todo o nada: si alguna fila tiene error, no se guarda ninguna.
"""
from __future__ import annotations

import unicodedata
from datetime import datetime, timezone
from typing import Any

from app.repositories.client import db, execute
from app.security.deps import AuthUser
from app.services.audit import RequestMeta, record
from app.services.costing import set_average_cost, weighted_average
from app.services.inventory import MODULE_ADJUSTMENTS, _next_code

REASON = "Stock inicial"


def _norm(s: Any) -> str:
    s = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def _num(v: Any) -> float | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n == n else None  # descarta NaN


def import_opening_stock(rows: list[dict[str, Any]], *, dry_run: bool, actor: AuthUser, meta: RequestMeta | None) -> dict[str, Any]:
    products = execute(db().table("products").select("id, sku, name, average_cost")).data or []
    warehouses = execute(db().table("warehouses").select("id, name, code")).data or []
    by_sku = {_norm(p["sku"]): p for p in products}
    by_pname = {_norm(p.get("name")): p for p in products}
    by_wh = {**{_norm(w.get("code")): w for w in warehouses if w.get("code")}, **{_norm(w["name"]): w for w in warehouses}}

    results: list[dict[str, Any]] = []
    valid: list[tuple[dict[str, Any], dict, dict, float, float]] = []
    seen: dict[tuple[str, str], int] = {}
    for i, raw in enumerate(rows, start=1):
        errors, fields = [], []
        p = by_sku.get(_norm(raw.get("sku"))) or by_pname.get(_norm(raw.get("sku")))
        if not p:
            errors.append(f"Producto: no existe «{raw.get('sku') or ''}». Cárgalo primero en Productos.")
            fields.append("sku")
        w = by_wh.get(_norm(raw.get("warehouse")))
        if not w:
            errors.append(f"Almacén: no existe «{raw.get('warehouse') or ''}».")
            fields.append("warehouse")
        qty = _num(raw.get("quantity"))
        if qty is None or qty <= 0:
            errors.append("Cantidad: debe ser mayor a 0.")
            fields.append("quantity")
        cost = _num(raw.get("unitCost"))
        if cost is None or cost < 0:
            errors.append("Costo unitario: debe ser 0 o más.")
            fields.append("unit_cost")
        if not errors:
            key = (p["id"], w["id"])
            if key in seen:
                # Número de fila de Excel (la fila 1 son los encabezados).
                errors.append(f"Producto y almacén repetidos en el archivo (igual a la fila {seen[key] + 1})")
                fields += ["sku", "warehouse"]
            else:
                seen[key] = i
        if errors:
            results.append({"row": i, "action": "error", "errors": errors, "fields": fields, "id": None})
        else:
            results.append({"row": i, "action": "create", "errors": [], "fields": [], "id": None})
            valid.append((results[-1], p, w, qty, cost))

    # Lo que ya tiene movimientos en ese almacén no es «inicial»: se omite para no duplicar stock.
    stock_by_product: dict[str, float] = {}
    if valid:
        ids = sorted({p["id"] for _, p, _, _, _ in valid})
        movs = execute(db().table("inventory_movements").select("product_id, warehouse_id, quantity_delta").in_("product_id", ids)).data or []
        used = {(m["product_id"], m["warehouse_id"]) for m in movs}
        for m in movs:
            stock_by_product[m["product_id"]] = stock_by_product.get(m["product_id"], 0) + float(m.get("quantity_delta") or 0)
        for res, p, w, _, _ in valid:
            if (p["id"], w["id"]) in used:
                res["action"] = "skip"
                res["errors"] = []
                res["note"] = "Ya tiene movimientos en este almacén: se omite"
        valid = [v for v in valid if v[0]["action"] == "create"]

    summary = {a: sum(1 for r in results if r["action"] == a) for a in ("create", "update", "error", "skip")}
    if dry_run or summary["error"]:
        return {"results": results, "summary": summary, "saved": False}

    now = datetime.now(timezone.utc).isoformat()
    avg = {p["id"]: float(p.get("average_cost") or 0) for _, p, _, _, _ in valid}
    for res, p, w, qty, cost in valid:
        code = _next_code("AJ")
        adj = execute(db().table("inventory_adjustments").insert({
            "code": code, "product_id": p["id"], "warehouse_id": w["id"], "adjustment_type": "SURPLUS",
            "reason": REASON, "created_by_user_id": actor.id,
        })).data[0]
        execute(db().table("inventory_movements").insert({
            "occurred_at": now, "product_id": p["id"], "warehouse_id": w["id"], "movement_type": "ADJUSTMENT",
            "quantity_delta": qty, "unit_cost": cost, "adjustment_id": adj["id"], "reference_snapshot": code,
            "created_by_user_id": actor.id,
        }))
        stock = stock_by_product.get(p["id"], 0)
        avg[p["id"]] = weighted_average(stock, avg[p["id"]], qty, cost)
        stock_by_product[p["id"]] = stock + qty
        res["id"] = adj["id"]
    for pid, value in avg.items():
        set_average_cost(pid, value)

    record(actor.actor, action="CREATE", module_key=MODULE_ADJUSTMENTS, entity_type="InventoryAdjustment", entity_id=None,
           reference=f"Stock inicial · {len(valid)} filas",
           description=f"Stock inicial importado: {len(valid)} productos/almacén, {summary['skip']} omitidos", meta=meta)
    return {"results": results, "summary": summary, "saved": True}