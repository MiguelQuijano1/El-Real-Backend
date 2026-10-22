"""Importación masiva de maestros desde un archivo (CSV/Excel leído en el navegador).

- Cada fila se valida con el MISMO modelo Pydantic del alta normal (mismas reglas: RUC de 11, DNI de 8…).
- No duplica: si ya existe un registro con la misma clave natural (RUC, DNI, SKU, placa…), se actualiza.
- Todo o nada: si alguna fila tiene error, no se guarda ninguna; el usuario corrige el archivo y reintenta.
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.repositories.client import db, execute
from app.security.deps import AuthUser
from app.services.audit import RequestMeta, record
from app.services.masters import MasterConfig, _next_code

# Nombres legibles de los campos para los mensajes de error (el resto se muestra tal cual).
_LABELS = {
    "legal_name": "Razón social", "document_type": "Tipo de documento", "document_number": "N.º de documento",
    "tax_id": "RUC", "billing_email": "Correo", "email": "Correo", "phone": "Teléfono", "sku": "SKU", "name": "Nombre",
    "unit": "Unidad", "sale_price": "Precio", "full_name": "Nombre", "national_id": "DNI", "license_number": "Licencia",
    "license_category": "Categoría de licencia", "plate": "Placa", "vehicle_type": "Tipo de vehículo",
    "warehouse_type": "Tipo de almacén", "unit_cost": "Costo", "supplier_id": "Proveedor", "product_id": "Producto",
    "payment_terms_days": "Días de crédito", "credit_limit": "Línea de crédito",
}


def _snake(name: str) -> str:
    return "".join("_" + c.lower() if c.isupper() else c for c in name)


def _messages(e: ValidationError) -> tuple[list[str], list[str]]:
    """Mensajes legibles y los campos (snake_case) donde está el error, para marcar la celda exacta."""
    out, fields = [], []
    for err in e.errors():
        loc = [str(p) for p in err.get("loc", ())]
        name = _snake(loc[-1]) if loc else ""
        field = _LABELS.get(name, loc[-1]) if loc else ""
        msg = str(err.get("msg", "")).removeprefix("Value error, ")
        if err.get("type") == "missing":
            msg = "es obligatorio"
        out.append(f"{field}: {msg}" if field else msg)
        if name:
            fields.append(name)
        elif "RUC" in msg or "DNI" in msg:
            fields.append("document_number")  # validación del documento del cliente (sin campo en el error)
    return out, fields


def _key(cfg: MasterConfig, data: dict[str, Any]) -> tuple:
    return tuple(str(data.get(k) or "").strip().upper() for k in cfg.import_key)


def import_rows(cfg: MasterConfig, model: type, rows: list[dict[str, Any]], *, dry_run: bool,
                actor: AuthUser, meta: RequestMeta | None, fields: list[str] | None = None) -> dict[str, Any]:
    # Columnas que se pueden cambiar al actualizar (en snake_case, como en la tabla).
    allowed = {_snake(f) for f in fields} | set(cfg.import_key) if fields else None
    results: list[dict[str, Any]] = []
    valid: list[tuple[int, dict[str, Any]]] = []
    seen: dict[tuple, int] = {}

    for i, raw in enumerate(rows, start=1):
        try:
            data = model.model_validate(raw).model_dump(mode="json")
        except ValidationError as e:
            msgs, bad = _messages(e)
            results.append({"row": i, "action": "error", "errors": msgs, "fields": bad, "id": None})
            continue
        k = _key(cfg, data)
        if cfg.import_key and k in seen:
            # El mensaje usa el número de fila de Excel (la fila 1 son los encabezados).
            results.append({"row": i, "action": "error", "errors": [f"Fila repetida en el archivo (igual a la fila {seen[k] + 1})"],
                            "fields": list(cfg.import_key), "id": None})
            continue
        seen[k] = i
        valid.append((i, data))
        results.append({"row": i, "action": "create", "errors": [], "id": None})

    # Registros que ya existen con la misma clave natural (una consulta por lote).
    existing: dict[tuple, str] = {}
    if cfg.import_key and valid:
        q = db().table(cfg.table).select("id," + ",".join(cfg.import_key))
        for col in cfg.import_key:
            q = q.in_(col, sorted({str(d.get(col) or "") for _, d in valid}))
        for r in execute(q).data or []:
            existing[_key(cfg, r)] = r["id"]
    by_row = {r["row"]: r for r in results}
    for i, data in valid:
        hit = existing.get(_key(cfg, data))
        if hit:
            by_row[i]["action"] = "update"
            by_row[i]["id"] = hit

    summary = {a: sum(1 for r in results if r["action"] == a) for a in ("create", "update", "error")}
    if dry_run or summary["error"]:
        return {"results": results, "summary": summary, "saved": False}

    for i, data in valid:
        res = by_row[i]
        if res["action"] == "update":
            data.pop("code", None)  # el código interno (CLI-00001…) no se cambia al importar
            if allowed is not None:
                data = {k: v for k, v in data.items() if k in allowed}
            execute(db().table(cfg.table).update(data).eq("id", res["id"]))
        else:
            if cfg.code_prefix and not data.get("code"):
                data["code"] = _next_code(cfg.code_prefix)
            res["id"] = execute(db().table(cfg.table).insert(data)).data[0]["id"]

    record(actor.actor, action="CREATE", module_key=cfg.module, entity_type=cfg.entity, entity_id=None,
           reference=f"Importación · {len(valid)} filas",
           description=f"Importación de {cfg.label}: {summary['create']} nuevos, {summary['update']} actualizados", meta=meta)
    return {"results": results, "summary": summary, "saved": True}