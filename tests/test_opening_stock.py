"""Stock inicial desde el importador: ajuste «Sobrante» + movimiento en Kardex, con costo, y sin duplicar."""
from app.services import opening_stock as svc
from tests.test_master_import import _DB


class _Actor:
    actor = None
    id = "u1"


def _run(monkeypatch, tables, rows, dry_run):
    fake = _DB(tables)
    costs: dict[str, float] = {}
    monkeypatch.setattr(svc, "db", lambda: fake)
    monkeypatch.setattr(svc, "_next_code", lambda prefix: f"{prefix}-{len(fake.tables.get('inventory_adjustments', [])) + 1}")
    monkeypatch.setattr(svc, "record", lambda *a, **k: None)
    monkeypatch.setattr(svc, "set_average_cost", lambda pid, v: costs.__setitem__(pid, v))
    res = svc.import_opening_stock(rows, dry_run=dry_run, actor=_Actor(), meta=None)
    return res, fake, costs


BASE = {
    "products": [{"id": "p1", "sku": "CEM-001", "name": "Cemento", "average_cost": 0}],
    "warehouses": [{"id": "w1", "name": "Almacén Central"}],
    "inventory_movements": [],
}


def _tables():
    return {k: [dict(r) for r in v] for k, v in BASE.items()}


def test_simulacion_valida_sin_escribir(monkeypatch):
    rows = [{"sku": "CEM-001", "warehouse": "almacén central", "quantity": 100, "unitCost": 26.5},
            {"sku": "NO-EXISTE", "warehouse": "Almacén Central", "quantity": 5, "unitCost": 1},
            {"sku": "CEM-001", "warehouse": "Otro", "quantity": 0, "unitCost": -1}]
    res, fake, _ = _run(monkeypatch, _tables(), rows, dry_run=True)
    assert [r["action"] for r in res["results"]] == ["create", "error", "error"]
    assert res["results"][1]["fields"] == ["sku"]
    assert set(res["results"][2]["fields"]) >= {"warehouse", "quantity", "unit_cost"}
    assert fake.tables["inventory_movements"] == [] and res["saved"] is False


def test_importar_crea_ajuste_movimiento_y_costo(monkeypatch):
    rows = [{"sku": "CEM-001", "warehouse": "Almacén Central", "quantity": 100, "unitCost": 26.5}]
    res, fake, costs = _run(monkeypatch, _tables(), rows, dry_run=False)
    assert res["saved"] is True and res["summary"]["create"] == 1
    adj = fake.tables["inventory_adjustments"][0]
    assert adj["adjustment_type"] == "SURPLUS" and adj["reason"] == "Stock inicial"
    mov = fake.tables["inventory_movements"][0]
    assert mov["movement_type"] == "ADJUSTMENT" and mov["quantity_delta"] == 100 and mov["unit_cost"] == 26.5
    assert mov["adjustment_id"] == adj["id"]
    assert costs["p1"] == 26.5  # sin stock previo, el costo promedio es el de la entrada


def test_si_ya_tiene_movimientos_se_omite(monkeypatch):
    t = _tables()
    t["inventory_movements"] = [{"product_id": "p1", "warehouse_id": "w1", "quantity_delta": 10}]
    rows = [{"sku": "CEM-001", "warehouse": "Almacén Central", "quantity": 100, "unitCost": 26.5}]
    res, fake, _ = _run(monkeypatch, t, rows, dry_run=False)
    assert res["results"][0]["action"] == "skip"
    assert len(fake.tables["inventory_movements"]) == 1  # no se duplicó


def test_mismo_producto_y_almacen_dos_veces_es_error(monkeypatch):
    rows = [{"sku": "CEM-001", "warehouse": "Almacén Central", "quantity": 1, "unitCost": 1},
            {"sku": "cem-001", "warehouse": "ALMACÉN CENTRAL", "quantity": 2, "unitCost": 1}]
    res, _, _ = _run(monkeypatch, _tables(), rows, dry_run=True)
    assert res["results"][1]["action"] == "error" and "fila 2" in res["results"][1]["errors"][0]