"""Costo promedio ponderado del producto (`company_settings.cost_method = WEIGHTED_AVERAGE`).

El costo vive en `products.average_cost` y no lo mantiene ningún trigger de la base: lo actualiza el backend
al registrar una recepción de compra. Las salidas (despacho) valorizan al costo promedio vigente y no lo cambian.
"""
from __future__ import annotations

from app.repositories.client import db, execute, first, rows


def weighted_average(stock: float, avg: float, qty: float, unit_cost: float) -> float:
    """Costo promedio después de una entrada de `qty` unidades a `unit_cost`.

    Si no hay stock (o es negativo) el costo es el de la entrada. Lo mismo si hay stock con costo desconocido
    (`avg` = 0, p. ej. entradas anteriores a este cálculo): no se promedia contra un cero que no es un costo real.
    """
    if qty <= 0:
        return avg
    if stock <= 0 or avg <= 0:
        return round(unit_cost, 6)
    return round((stock * avg + qty * unit_cost) / (stock + qty), 6)


def product_stock_total(product_id: str) -> float:
    """Stock actual del producto sumando todos los almacenes."""
    movs = rows(db().table("inventory_movements").select("quantity_delta").eq("product_id", product_id))
    return sum(float(m.get("quantity_delta") or 0) for m in movs)


def get_average_cost(product_id: str) -> float:
    p = first(db().table("products").select("average_cost").eq("id", product_id))
    return float((p or {}).get("average_cost") or 0)


def set_average_cost(product_id: str, value: float) -> None:
    execute(db().table("products").update({"average_cost": value}).eq("id", product_id))
