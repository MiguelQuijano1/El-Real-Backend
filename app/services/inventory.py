from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.core.errors import bad_request, not_found
from app.repositories.client import db, execute, first, rows
from app.security.deps import AuthUser
from app.services.audit import RequestMeta, record

MODULE_STOCK = "stock"
MODULE_KARDEX = "kardex"
MODULE_TRANSFERS = "transfers"
MODULE_ADJUSTMENTS = "adjustments"

TYPE_UI = {
    "RECEIPT": "Entrada",
    "DISPATCH": "Salida",
    "TRANSFER": "Transferencia",
    "ADJUSTMENT": "Ajuste",
}
ADJ_UI = {"LOSS": "Merma", "SURPLUS": "Sobrante", "EXPIRY": "Vencimiento", "CORRECTION": "Corrección"}
UI_ADJ = {v: k for k, v in ADJ_UI.items()}
TR_UI = {"PENDING": "Pendiente", "IN_TRANSIT": "En tránsito", "RECEIVED": "Recibida", "CANCELLED": "Cancelada"}
UI_TR = {v: k for k, v in TR_UI.items()}


def _next_code(prefix: str) -> str:
    y = datetime.now(timezone.utc).year
    n = execute(db().rpc("next_sequence", {"p_type": prefix, "p_year": y, "p_series": ""})).data
    n = n[0] if isinstance(n, list) else n
    return f"{prefix}-{y}-{int(n):04d}"


def _stock_status(stock: float, minimum: float) -> str:
    if stock <= 0:
        return "Sin stock"
    if minimum > 0 and stock < minimum:
        return "Bajo mínimo"
    if minimum > 0 and stock > minimum * 3:
        return "Sobrestock"
    return "Normal"


# --------------------------------------------------------------------------- stock
def list_stock(*, q: str | None = None, warehouse_id: str | None = None, limit: int = 200, offset: int = 0) -> dict[str, Any]:
    """Saldo actual = SUM(quantity_delta) por producto y almacén."""
    movs = rows(
        db().table("inventory_movements").select("product_id, warehouse_id, quantity_delta")
    )
    balances: dict[tuple[str, str], float] = defaultdict(float)
    for m in movs:
        key = (m["product_id"], m["warehouse_id"])
        balances[key] += float(m.get("quantity_delta") or 0)

    products = {p["id"]: p for p in rows(db().table("products").select("id, sku, name, category_id, average_cost, minimum_stock, status").eq("status", "ACTIVE"))}
    warehouses = {w["id"]: w for w in rows(db().table("warehouses").select("id, code, name, status"))}
    categories = {c["id"]: c for c in rows(db().table("product_categories").select("id, name"))}

    items = []
    for (pid, wid), qty in balances.items():
        if warehouse_id and wid != warehouse_id:
            continue
        p = products.get(pid)
        w = warehouses.get(wid)
        if not p or not w:
            continue
        if w.get("status") == "INACTIVE":
            continue
        min_s = float(p.get("minimum_stock") or 0)
        cost = float(p.get("average_cost") or 0)
        cat = categories.get(p.get("category_id") or "")
        row = {
            "productId": pid,
            "warehouseId": wid,
            "sku": p.get("sku"),
            "prod": p.get("name"),
            "cat": (cat or {}).get("name") or "",
            "alm": w.get("name"),
            "stock": qty,
            "min": min_s,
            "res": 0,
            "disp": qty,
            "valor": round(qty * cost, 2),
            "estado": _stock_status(qty, min_s),
        }
        if q:
            term = q.strip().lower()
            if term not in f"{row['sku']} {row['prod']} {row['alm']}".lower():
                continue
        items.append(row)

    # También productos activos sin movimiento en almacenes activos (stock 0) — opcional: solo si hay pocos
    # Por rendimiento solo devolvemos filas con movimiento o stock != 0; el front ya lista lo existente.

    items.sort(key=lambda r: (r["alm"], r["prod"]))
    total = len(items)
    slice_ = items[offset : offset + limit]
    return {"items": slice_, "total": total, "limit": limit, "offset": offset}


# --------------------------------------------------------------------------- kardex
def list_kardex(
    *,
    product_id: str | None = None,
    warehouse_id: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    query = db().table("inventory_movements").select(
        "*, product:products(id, sku, name), warehouse:warehouses(id, name)",
        count="exact",
    )
    if product_id:
        query = query.eq("product_id", product_id)
    if warehouse_id:
        query = query.eq("warehouse_id", warehouse_id)
    res = execute(query.order("occurred_at", desc=True).order("id", desc=True).range(offset, offset + limit - 1))
    items = []
    # Para saldo: cargar movimientos más antiguos y calcular running — simplificado: saldo solo del lote actual
    for m in res.data or []:
        p = m.get("product") or {}
        w = m.get("warehouse") or {}
        delta = float(m.get("quantity_delta") or 0)
        tipo = TYPE_UI.get(m.get("movement_type") or "", m.get("movement_type") or "")
        row = {
            "id": m["id"],
            "fecha": str(m.get("occurred_at") or "")[:19].replace("T", " "),
            "sku": p.get("sku") or "",
            "prod": p.get("name") or "",
            "alm": w.get("name") or "",
            "tipo": tipo,
            "cant": abs(delta),
            "signo": "+" if delta >= 0 else "−",
            "delta": delta,
            "costo": float(m.get("unit_cost") or 0),
            "ref": m.get("reference_snapshot") or "",
            "productId": m.get("product_id"),
            "warehouseId": m.get("warehouse_id"),
        }
        if q:
            term = q.strip().lower()
            if term not in f"{row['sku']} {row['prod']} {row['ref']} {row['alm']}".lower():
                continue
        items.append(row)
    return {"items": items, "total": res.count or len(items), "limit": limit, "offset": offset}


# --------------------------------------------------------------------------- transfers
def list_transfers(*, q: str | None = None, status: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    query = db().table("inventory_transfers").select(
        "*, lines:inventory_transfer_lines(*), "
        "source:warehouses!inventory_transfers_source_warehouse_id_fkey(id, name), "
        "dest:warehouses!inventory_transfers_destination_warehouse_id_fkey(id, name)",
        count="exact",
    )
    if status:
        key = UI_TR.get(status, status)
        if key in TR_UI:
            query = query.eq("status", key)
    res = execute(query.order("created_at", desc=True).range(offset, offset + limit - 1))
    items = []
    for t in res.data or []:
        lines = sorted(t.get("lines") or [], key=lambda x: x.get("line_no", 0))
        src = t.get("source") or {}
        dst = t.get("dest") or {}
        # fallback sin nombre de FK
        if not src and t.get("source_warehouse_id"):
            src = first(db().table("warehouses").select("id, name").eq("id", t["source_warehouse_id"])) or {}
        if not dst and t.get("destination_warehouse_id"):
            dst = first(db().table("warehouses").select("id, name").eq("id", t["destination_warehouse_id"])) or {}
        st = t.get("status") or "PENDING"
        items.append({
            "id": t["id"],
            "code": t["code"],
            "fecha": str(t.get("created_at") or "")[:10],
            "origen": src.get("name") or "",
            "destino": dst.get("name") or "",
            "estado": TR_UI.get(st, st),
            "statusKey": st,
            "items": f"{len(lines)} ítem(s)",
            "lines": [
                {
                    "sku": ln.get("sku_snapshot"),
                    "name": ln.get("name_snapshot"),
                    "unit": ln.get("unit_snapshot"),
                    "qty": float(ln.get("quantity") or 0),
                    "productId": ln.get("product_id"),
                }
                for ln in lines
            ],
            "sourceWarehouseId": t.get("source_warehouse_id"),
            "destinationWarehouseId": t.get("destination_warehouse_id"),
        })
    if q:
        term = q.strip().lower()
        items = [i for i in items if term in f"{i['code']} {i['origen']} {i['destino']}".lower()]
    return {"items": items, "total": res.count or len(items), "limit": limit, "offset": offset}


def get_transfer(id_or_code: str) -> dict[str, Any]:
    data = list_transfers(q=None, limit=500, offset=0)
    for i in data["items"]:
        if i["id"] == id_or_code or i["code"] == id_or_code:
            return i
    raise not_found("Transferencia no encontrada")


def create_transfer(payload: dict[str, Any], actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    src = payload["source_warehouse_id"]
    dst = payload["destination_warehouse_id"]
    if src == dst:
        raise bad_request("Origen y destino deben ser distintos")
    lines_in = payload.get("lines") or []
    if not lines_in:
        raise bad_request("La transferencia debe tener al menos una línea")
    code = _next_code("TR")
    row = {
        "code": code,
        "source_warehouse_id": src,
        "destination_warehouse_id": dst,
        "responsible_user_id": payload.get("responsible_user_id") or actor.id,
        "status": "PENDING",
    }
    created = execute(db().table("inventory_transfers").insert(row)).data[0]
    tid = created["id"]
    line_rows = []
    for i, ln in enumerate(lines_in, start=1):
        line_rows.append({
            "transfer_id": tid,
            "line_no": i,
            "product_id": ln["product_id"],
            "sku_snapshot": ln["sku"],
            "name_snapshot": ln["name"],
            "unit_snapshot": ln["unit"],
            "quantity": float(ln["quantity"]),
        })
    execute(db().table("inventory_transfer_lines").insert(line_rows))
    record(actor.actor, action="CREATE", module_key=MODULE_TRANSFERS, entity_type="InventoryTransfer",
           entity_id=tid, reference=code, description=f"Transferencia creada · {code}", meta=meta)
    return get_transfer(tid)


def _balance(product_id: str, warehouse_id: str) -> float:
    movs = rows(
        db().table("inventory_movements")
        .select("quantity_delta")
        .eq("product_id", product_id)
        .eq("warehouse_id", warehouse_id)
    )
    return sum(float(m.get("quantity_delta") or 0) for m in movs)


def dispatch_transfer(id_or_code: str, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    t = first(db().table("inventory_transfers").select("*").eq("id", id_or_code)) or first(
        db().table("inventory_transfers").select("*").eq("code", id_or_code)
    )
    if not t:
        raise not_found("Transferencia no encontrada")
    if t.get("status") != "PENDING":
        raise bad_request("Solo se puede despachar una transferencia pendiente")
    lines = rows(db().table("inventory_transfer_lines").select("*").eq("transfer_id", t["id"]).order("line_no"))
    now = datetime.now(timezone.utc).isoformat()
    for ln in lines:
        bal = _balance(ln["product_id"], t["source_warehouse_id"])
        qty = float(ln["quantity"])
        if bal < qty:
            raise bad_request(f"Stock insuficiente de {ln['sku_snapshot']} en origen (hay {bal}, pide {qty})")
        execute(db().table("inventory_movements").insert({
            "occurred_at": now,
            "product_id": ln["product_id"],
            "warehouse_id": t["source_warehouse_id"],
            "movement_type": "TRANSFER",
            "quantity_delta": -qty,
            "unit_cost": 0,
            "transfer_line_id": ln["id"],
            "movement_leg": "OUT",
            "reference_snapshot": t["code"],
            "created_by_user_id": actor.id,
        }))
    execute(db().table("inventory_transfers").update({
        "status": "IN_TRANSIT", "dispatched_at": now,
    }).eq("id", t["id"]))
    record(actor.actor, action="EDIT", module_key=MODULE_TRANSFERS, entity_type="InventoryTransfer",
           entity_id=t["id"], reference=t["code"], description=f"Transferencia despachada · {t['code']}", meta=meta)
    return get_transfer(t["id"])


def receive_transfer(id_or_code: str, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    t = first(db().table("inventory_transfers").select("*").eq("id", id_or_code)) or first(
        db().table("inventory_transfers").select("*").eq("code", id_or_code)
    )
    if not t:
        raise not_found("Transferencia no encontrada")
    if t.get("status") != "IN_TRANSIT":
        raise bad_request("Solo se puede recibir una transferencia en tránsito")
    lines = rows(db().table("inventory_transfer_lines").select("*").eq("transfer_id", t["id"]).order("line_no"))
    now = datetime.now(timezone.utc).isoformat()
    for ln in lines:
        qty = float(ln["quantity"])
        execute(db().table("inventory_movements").insert({
            "occurred_at": now,
            "product_id": ln["product_id"],
            "warehouse_id": t["destination_warehouse_id"],
            "movement_type": "TRANSFER",
            "quantity_delta": qty,
            "unit_cost": 0,
            "transfer_line_id": ln["id"],
            "movement_leg": "IN",
            "reference_snapshot": t["code"],
            "created_by_user_id": actor.id,
        }))
    execute(db().table("inventory_transfers").update({
        "status": "RECEIVED", "received_at": now,
    }).eq("id", t["id"]))
    record(actor.actor, action="EDIT", module_key=MODULE_TRANSFERS, entity_type="InventoryTransfer",
           entity_id=t["id"], reference=t["code"], description=f"Transferencia recibida · {t['code']}", meta=meta)
    return get_transfer(t["id"])


def cancel_transfer(id_or_code: str, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    t = first(db().table("inventory_transfers").select("*").eq("id", id_or_code)) or first(
        db().table("inventory_transfers").select("*").eq("code", id_or_code)
    )
    if not t:
        raise not_found("Transferencia no encontrada")
    if t.get("status") not in ("PENDING",):
        raise bad_request("Solo se puede cancelar una transferencia pendiente")
    execute(db().table("inventory_transfers").update({"status": "CANCELLED"}).eq("id", t["id"]))
    record(actor.actor, action="EDIT", module_key=MODULE_TRANSFERS, entity_type="InventoryTransfer",
           entity_id=t["id"], reference=t["code"], description=f"Transferencia cancelada · {t['code']}", meta=meta)
    return get_transfer(t["id"])


# --------------------------------------------------------------------------- adjustments
def list_adjustments(*, q: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    query = db().table("inventory_adjustments").select(
        "*, product:products(id, sku, name), warehouse:warehouses(id, name)",
        count="exact",
    )
    res = execute(query.order("created_at", desc=True).range(offset, offset + limit - 1))
    items = []
    for a in res.data or []:
        p = a.get("product") or {}
        w = a.get("warehouse") or {}
        # cantidad desde el movimiento ligado
        mov = first(db().table("inventory_movements").select("quantity_delta").eq("adjustment_id", a["id"]))
        delta = float((mov or {}).get("quantity_delta") or 0)
        tipo = ADJ_UI.get(a.get("adjustment_type") or "", a.get("adjustment_type") or "")
        items.append({
            "id": a["id"],
            "code": a["code"],
            "fecha": str(a.get("created_at") or "")[:10],
            "sku": p.get("sku") or "",
            "prod": p.get("name") or "",
            "alm": w.get("name") or "",
            "tipo": tipo,
            "cant": abs(delta),
            "delta": delta,
            "motivo": a.get("reason") or "",
            "productId": a.get("product_id"),
            "warehouseId": a.get("warehouse_id"),
        })
    if q:
        term = q.strip().lower()
        items = [i for i in items if term in f"{i['code']} {i['prod']} {i['sku']} {i['motivo']}".lower()]
    return {"items": items, "total": res.count or len(items), "limit": limit, "offset": offset}


def create_adjustment(payload: dict[str, Any], actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    product_id = payload["product_id"]
    warehouse_id = payload["warehouse_id"]
    adj_type = payload.get("adjustment_type") or "CORRECTION"
    if adj_type not in ADJ_UI:
        adj_type = UI_ADJ.get(adj_type, "CORRECTION")
    qty = abs(float(payload.get("quantity") or 0))
    if qty <= 0:
        raise bad_request("La cantidad debe ser mayor a 0")
    # Merma, vencimiento, corrección restan; sobrante suma
    if adj_type == "SURPLUS":
        delta = qty
    else:
        delta = -qty
        bal = _balance(product_id, warehouse_id)
        if bal + delta < 0:
            raise bad_request(f"Stock insuficiente para el ajuste (hay {bal})")

    product = first(db().table("products").select("sku, name, unit, average_cost").eq("id", product_id))
    if not product:
        raise bad_request("Producto no encontrado")
    code = _next_code("AJ")
    adj = execute(db().table("inventory_adjustments").insert({
        "code": code,
        "product_id": product_id,
        "warehouse_id": warehouse_id,
        "adjustment_type": adj_type,
        "reason": payload.get("reason") or "Ajuste de inventario",
        "created_by_user_id": actor.id,
    })).data[0]
    now = datetime.now(timezone.utc).isoformat()
    execute(db().table("inventory_movements").insert({
        "occurred_at": now,
        "product_id": product_id,
        "warehouse_id": warehouse_id,
        "movement_type": "ADJUSTMENT",
        "quantity_delta": delta,
        "unit_cost": float(product.get("average_cost") or 0),
        "adjustment_id": adj["id"],
        "reference_snapshot": code,
        "created_by_user_id": actor.id,
    }))
    record(actor.actor, action="CREATE", module_key=MODULE_ADJUSTMENTS, entity_type="InventoryAdjustment",
           entity_id=adj["id"], reference=code, description=f"Ajuste {ADJ_UI.get(adj_type, adj_type)} · {code}", meta=meta)
    data = list_adjustments(limit=1, offset=0)
    for i in data["items"]:
        if i["id"] == adj["id"]:
            return i
    return {"id": adj["id"], "code": code}