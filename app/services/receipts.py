from __future__ import annotations

from typing import Any

from app.core.errors import not_found
from app.repositories.client import db, execute, first

CONDITION_UI = {"CONFORMING": "Conforme", "OBSERVATIONS": "Con observaciones"}


def list_receipts(*, q: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Lista recepciones reales + órdenes de compra aprobadas aún sin recepción."""
    # Recepciones registradas
    query = db().table("goods_receipts").select(
        "*, order:business_orders(id, code, party_name, current_step, is_cancelled), warehouse:warehouses(id, name)",
        count="exact",
    )
    res = execute(query.order("received_at", desc=True).range(offset, offset + limit - 1))
    items = []
    received_order_ids = set()
    for r in res.data or []:
        order = r.get("order") or {}
        warehouse = r.get("warehouse") or {}
        received_order_ids.add(r.get("order_id"))
        cond = r.get("condition") or "CONFORMING"
        items.append({
            "id": r["id"],
            "ni": r.get("receipt_number"),
            "fecha": str(r.get("received_at") or "")[:10],
            "oc": order.get("code") or "",
            "prov": order.get("party_name") or "",
            "alm": warehouse.get("name") or "",
            "items": "",
            "estado": CONDITION_UI.get(cond, cond),
            "recibe": "",
            "obs": r.get("observations") or "",
            "orderId": r.get("order_id"),
        })

    # Órdenes de compra aprobadas (step >= 2) sin recepción
    pending = execute(
        db().table("business_orders")
        .select("id, code, party_name, warehouse_id, current_step, warehouse:warehouses(name)")
        .eq("kind", "PURCHASE")
        .eq("is_cancelled", False)
        .gte("current_step", 2)
        .lt("current_step", 3)
    ).data or []
    for o in pending:
        if o["id"] in received_order_ids:
            continue
        wh = o.get("warehouse") or {}
        items.append({
            "id": f"pending-{o['id']}",
            "ni": "—",
            "fecha": "",
            "oc": o.get("code") or "",
            "prov": o.get("party_name") or "",
            "alm": wh.get("name") or "",
            "items": "",
            "estado": "Por recibir",
            "recibe": "—",
            "obs": "",
            "orderId": o["id"],
        })

    if q:
        term = q.strip().lower()
        items = [i for i in items if term in (i["oc"] + i["prov"] + i["ni"]).lower()]

    return {"items": items, "total": len(items), "limit": limit, "offset": offset}


def get_receipt(receipt_id: str) -> dict[str, Any]:
    row = first(
        db().table("goods_receipts").select(
            "*, order:business_orders(id, code, party_name), warehouse:warehouses(id, name)"
        ).eq("id", receipt_id)
    )
    if not row:
        raise not_found("Recepción no encontrada")
    order = row.get("order") or {}
    warehouse = row.get("warehouse") or {}
    cond = row.get("condition") or "CONFORMING"
    return {
        "id": row["id"],
        "ni": row.get("receipt_number"),
        "fecha": str(row.get("received_at") or "")[:10],
        "oc": order.get("code") or "",
        "prov": order.get("party_name") or "",
        "alm": warehouse.get("name") or "",
        "estado": CONDITION_UI.get(cond, cond),
        "obs": row.get("observations") or "",
    }