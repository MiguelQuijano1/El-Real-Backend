"""Recalcula `products.average_cost` repasando el historial de movimientos (costo promedio ponderado).

Útil para productos con movimientos anteriores a que el backend calculara el costo (quedaron en 0).
Por defecto NO escribe nada: muestra qué cambiaría. Con --apply actualiza los productos.

Regla: las entradas (RECEIPT) promedian el costo; el resto de movimientos (despachos, ajustes, transferencias)
solo cambian el stock. Un stock sin costo conocido toma el costo de la siguiente entrada (ver app/services/costing.py).

Uso:  python -m scripts.recalc_average_cost            (vista previa)
      python -m scripts.recalc_average_cost --apply    (escribe)
"""
import sys
from collections import defaultdict

from app.repositories.client import db, rows
from app.services.costing import set_average_cost, weighted_average


def main(apply: bool) -> None:
    products = {p["id"]: p for p in rows(db().table("products").select("id, sku, name, average_cost"))}
    movs = rows(
        db().table("inventory_movements")
        .select("product_id, movement_type, quantity_delta, unit_cost, occurred_at, created_at")
        .order("occurred_at").order("created_at")
    )
    by_product: dict[str, list[dict]] = defaultdict(list)
    for m in movs:
        by_product[m["product_id"]].append(m)

    changes = 0
    for pid, items in by_product.items():
        stock, avg = 0.0, 0.0
        for m in items:
            qty = float(m["quantity_delta"])
            if m["movement_type"] == "RECEIPT" and qty > 0:
                avg = weighted_average(stock, avg, qty, float(m["unit_cost"]))
            stock += qty
        p = products.get(pid)
        if not p:
            continue
        current = float(p.get("average_cost") or 0)
        marker = "  " if abs(current - avg) < 1e-6 else "->"
        print(f"{marker} {p['sku']:<14} {p['name']:<24} stock={stock:g}  costo actual={current:g}  recalculado={avg:g}")
        if marker == "->":
            changes += 1
            if apply:
                set_average_cost(pid, avg)

    print(f"\n{changes} producto(s) {'actualizados' if apply else 'cambiarían (usa --apply para escribir)'}.")


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
