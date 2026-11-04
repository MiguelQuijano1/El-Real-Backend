"""Geocodificación de direcciones con Nominatim (OSM). Cachea resultados en memoria."""
import asyncio
import logging
from typing import Any

import httpx

from app.repositories.client import db, execute, first

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, float] | None] = {}
_LOCK = asyncio.Lock()
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_HEADERS = {"User-Agent": "EnterpriseCloud-ERP/1.0 (contact: admin@empresa.pe)"}


async def _http_get(url: str, params: dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(headers=_HEADERS, timeout=10) as client:
        return await client.get(url, params=params)


async def geocode(address: str) -> tuple[float, float] | None:
    """Convierte una dirección a (lat, lng). Devuelve None si no se encuentra.

    Cachea en memoria. Máximo 1 llamada/segundo a Nominatim (TOS).
    """
    key = address.strip().lower()
    if not key:
        return None
    if key in _CACHE:
        return _CACHE[key]

    async with _LOCK:
        # Doble check después de adquirir el lock
        if key in _CACHE:
            return _CACHE[key]
        try:
            resp = await _http_get(_NOMINATIM_URL, {
                "q": address, "format": "json", "limit": 1,
                "countrycodes": "pe", "addressdetails": 0,
            })
            resp.raise_for_status()
            results = resp.json()
            if results:
                coords = (float(results[0]["lat"]), float(results[0]["lon"]))
            else:
                coords = None
        except Exception as exc:
            logger.warning("Nominatim error para '%s': %s", address, exc)
            coords = None
        _CACHE[key] = coords
        await asyncio.sleep(1.0)  # TOS: 1 req/s
        return coords


async def geocode_order(order_id: str) -> bool:
    """Geocodifica la dirección de entrega de una orden y guarda delivery_lat/delivery_lng en BD.

    Devuelve True si encontró coordenadas, False si no.
    Llama solo si la orden no tiene coordenadas ya guardadas.
    """
    order = first(
        db().table("business_orders")
        .select("id, party_address, delivery_lat, delivery_lng")
        .eq("id", order_id)
    )
    if not order:
        return False
    if order.get("delivery_lat") is not None:
        return True  # ya tiene coords

    address = (order.get("party_address") or "").strip()
    if not address:
        return False

    coords = await geocode(address)
    if coords:
        lat, lng = coords
        execute(
            db().table("business_orders")
            .update({"delivery_lat": lat, "delivery_lng": lng})
            .eq("id", order_id)
        )
        return True
    return False


async def geocode_order_by_code(code: str) -> bool:
    """Como geocode_order pero recibe el code de la orden (OV-2026-XXXX)."""
    order = first(db().table("business_orders").select("id").eq("code", code))
    if not order:
        return False
    return await geocode_order(order["id"])
