"""Pruebas de los módulos de Gestión con una base de datos en memoria (no requieren Supabase ni PostgREST)."""
import re
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute

from app.main import app
from app.repositories import client as repo
from app.security import deps
from app.services import gestion as svc
from tests.test_route_policies import ROUTES, policies


# ───────────────────────── base de datos en memoria mínima ─────────────────────────
class Result:
    def __init__(self, data: Any):
        self.data = data


class Query:
    """Implementa solo lo que usa app/services/gestion.py (select, insert, upsert, update, delete, filtros, orden, rango)."""

    def __init__(self, db: "FakeDb", table: str):
        self.db, self.table = db, table
        self.op, self.payload, self.conflict = "select", None, None
        self.cols = "*"
        self.filters: list = []
        self.orders: list = []
        self.lim = self.start = None

    def select(self, cols="*", **_): self.cols = cols; return self
    def insert(self, data): self.op, self.payload = "insert", data; return self
    def upsert(self, data, on_conflict=None): self.op, self.payload, self.conflict = "upsert", data, on_conflict; return self
    def update(self, data): self.op, self.payload = "update", data; return self
    def delete(self): self.op = "delete"; return self
    def eq(self, c, v): self.filters.append((c, "eq", v)); return self
    def in_(self, c, v): self.filters.append((c, "in", list(v))); return self
    def order(self, c, desc=False): self.orders.append((c, desc)); return self
    def limit(self, n): self.lim = n; return self
    def range(self, a, b): self.start, self.lim = a, b - a + 1; return self

    def _match(self, row):
        for c, op, v in self.filters:
            if op == "eq" and row.get(c) != v:
                return False
            if op == "in" and row.get(c) not in v:
                return False
        return True

    def _embed(self, row):
        out = dict(row)
        for alias, table, _cols in re.findall(r"(\w+):(\w+)\(([^)]*)\)", self.cols):
            fk = {"warehouses": "warehouse_id"}[table]
            out[alias] = next((r for r in self.db.t(table) if r["id"] == row[fk]), None)
        return out

    def execute(self):
        t = self.db.t(self.table)
        if self.op == "insert":
            rows = self.payload if isinstance(self.payload, list) else [self.payload]
            made = [self.db.insert(self.table, r) for r in rows]
            return Result(made)
        if self.op == "upsert":
            keys = (self.conflict or "id").split(",")
            r = dict(self.payload)
            hit = next((x for x in t if all(x.get(k) == r.get(k) for k in keys)), None)
            if hit:
                hit.update(r)
                return Result([dict(hit)])
            return Result([self.db.insert(self.table, r)])
        if self.op == "update":
            hit = [r for r in t if self._match(r)]
            for r in hit:
                r.update(self.payload)
            return Result([dict(r) for r in hit])
        if self.op == "delete":
            gone = [r for r in t if self._match(r)]
            self.db.tables[self.table] = [r for r in t if r not in gone]
            for child in self.db.cascade.get(self.table, []):
                self.db.tables[child] = [r for r in self.db.t(child) if r.get("count_id") not in {g["id"] for g in gone}]
            return Result(gone)
        rows = [self._embed(r) for r in t if self._match(r)]
        for c, desc in reversed(self.orders):
            rows.sort(key=lambda r: (r.get(c) is None, r.get(c)), reverse=desc)
        if self.start:
            rows = rows[self.start:]
        if self.lim is not None:
            rows = rows[: self.lim]
        return Result(rows)


class FakeDb:
    UNIQUE = {"sales_goals": ("period", "kind", "target"), "stock_count_items": ("count_id", "sku")}
    cascade = {"stock_counts": ["stock_count_items"]}

    def __init__(self):
        self.tables: dict[str, list] = {}
        self.seq = 0

    def t(self, name): return self.tables.setdefault(name, [])

    def insert(self, table, row):
        row = dict(row)
        row.setdefault("id", str(uuid.uuid4()))
        key = self.UNIQUE.get(table)
        if key and any(all(x.get(k) == row.get(k) for k in key) for x in self.t(table)):
            from postgrest.exceptions import APIError
            raise APIError({"code": "23505", "message": "duplicate", "details": "", "hint": ""})
        row.setdefault("opened_at", "2026-10-28T10:00:00+00:00")
        row.setdefault("closed_at", None)
        row.setdefault("status", "OPEN") if table == "stock_counts" else None
        row.setdefault("counted_qty", None) if table == "stock_count_items" else None
        self.t(table).append(row)
        return dict(row)

    def table(self, name):
        if name == "stock_count_summary":
            return SummaryQuery(self)
        return Query(self, name)

    def rpc(self, name, params):
        self.seq += 1
        q = Query(self, "_")
        q.execute = lambda: Result(self.seq)
        return q


class SummaryQuery(Query):
    def __init__(self, db):
        super().__init__(db, "stock_count_items")

    def execute(self):
        by: dict[str, list] = {}
        for i in self.db.t("stock_count_items"):
            by.setdefault(i["count_id"], []).append(i)
        out = []
        for cid, items in by.items():
            counted = [i for i in items if i["counted_qty"] is not None]
            miss = sum((i["system_qty"] - i["counted_qty"]) * i["unit_cost"] for i in counted if i["counted_qty"] < i["system_qty"])
            sur = sum((i["counted_qty"] - i["system_qty"]) * i["unit_cost"] for i in counted if i["counted_qty"] > i["system_qty"])
            out.append({"count_id": cid, "item_count": len(items), "pending": len(items) - len(counted),
                        "with_diff": sum(1 for i in counted if i["counted_qty"] != i["system_qty"]),
                        "missing_value": miss, "surplus_value": sur})
        return Result([r for r in out if self._match(r)])


# ───────────────────────── fixtures ─────────────────────────
WAREHOUSE = {"id": "w1", "name": "Central", "status": "ACTIVE"}


@pytest.fixture()
def env(monkeypatch):
    fake = FakeDb()
    fake.tables["warehouses"] = [dict(WAREHOUSE)]
    fake.tables["products"] = [{"id": "p1", "unit": "Bolsa", "average_cost": 10}, {"id": "p2", "unit": "Saco", "average_cost": 2}]
    fake.tables["business_orders"] = [
        {"id": "o1", "code": "OV-1", "kind": "SALE", "is_cancelled": False, "current_step": 3},
        {"id": "o2", "code": "OV-2", "kind": "SALE", "is_cancelled": False, "current_step": 2},
    ]
    repo.set_client(fake)
    monkeypatch.setattr(svc.inventory_svc, "list_stock", lambda **_: {"items": [
        {"productId": "p1", "sku": "CEM", "prod": "Cemento", "stock": 10},
        {"productId": "p2", "sku": "ARE", "prod": "Arena", "stock": 5},
    ]})
    monkeypatch.setattr(svc, "record", lambda *a, **k: None)
    user = deps.AuthUser(id="u1", role_id="r1", full_name="Ana Admin", email="a@x.pe", area=None, status="ACTIVE",
                         last_login_at=None, role={"code": "ADMIN", "name": "Administrador", "is_active": True}, session_id="s1")
    app.dependency_overrides[deps.get_current_user] = lambda: user
    yield TestClient(app), fake
    app.dependency_overrides.clear()
    repo.set_client(None)


# ───────────────────────── políticas de acceso ─────────────────────────
def test_every_gestion_route_requires_the_expected_permission():
    wanted = {
        ("GET", "/api/v1/module-switches"): "authenticated",
        ("PUT", "/api/v1/module-switches/{module_id}"): ("settings", "edit"),
        ("GET", "/api/v1/sales-goals"): ("reports", "view"),
        ("POST", "/api/v1/sales-goals"): ("settings", "edit"),
        ("GET", "/api/v1/commissions"): ("reports", "view"),
        ("POST", "/api/v1/commissions/payouts"): ("settings", "edit"),
        ("GET", "/api/v1/stock-counts"): ("adjustments", "view"),
        ("POST", "/api/v1/stock-counts"): ("adjustments", "create"),
        ("POST", "/api/v1/stock-counts/{count_id}/close"): ("adjustments", "create"),
        ("GET", "/api/v1/deliveries"): ("sales-orders", "view"),
        ("PUT", "/api/v1/deliveries/{order_code}"): ("sales-orders", "edit"),
        ("GET", "/api/v1/cashflow/settings"): ("receivables", "view"),
        ("PUT", "/api/v1/cashflow/settings"): ("cash", "edit"),
    }
    found = {(m, path): policies(r.dependant) for path, r in ROUTES if isinstance(r, APIRoute) for m in r.methods}
    for key, policy in wanted.items():
        assert found.get(key) == [policy], key


# ───────────────────────── interruptores ─────────────────────────
def test_switches_default_on_and_persist(env):
    client, _ = env
    assert all(client.get("/api/v1/module-switches").json()["switches"].values())
    out = client.put("/api/v1/module-switches/metas", json={"enabled": False}).json()["switches"]
    assert out["metas"] is False and out["conteo"] is True
    assert client.put("/api/v1/module-switches/inexistente", json={"enabled": False}).status_code == 404


# ───────────────────────── metas ─────────────────────────
def test_goals_crud_duplicates_and_copy(env):
    client, _ = env
    body = {"period": "2026-10", "kind": "vendedor", "target": "Ana", "amount": 20000}
    g = client.post("/api/v1/sales-goals", json=body)
    assert g.status_code == 201 and g.json()["amount"] == 20000
    assert client.post("/api/v1/sales-goals", json=body).status_code == 409
    assert client.post("/api/v1/sales-goals", json={**body, "amount": 0}).status_code == 400
    assert client.post("/api/v1/sales-goals", json={**body, "period": "2026-13"}).status_code == 400

    gid = g.json()["id"]
    assert client.patch(f"/api/v1/sales-goals/{gid}", json={"amount": 25000}).json()["amount"] == 25000
    copied = client.post("/api/v1/sales-goals/copy", json={"fromPeriod": "2026-10", "toPeriod": "2026-11"}).json()
    assert copied["created"] == 1 and copied["items"][0]["period"] == "2026-11"
    again = client.post("/api/v1/sales-goals/copy", json={"fromPeriod": "2026-10", "toPeriod": "2026-11"}).json()
    assert again["created"] == 0
    assert [x["period"] for x in client.get("/api/v1/sales-goals?period=2026-10").json()["items"]] == ["2026-10"]
    assert client.delete(f"/api/v1/sales-goals/{gid}").status_code == 204
    assert client.get("/api/v1/sales-goals?period=2026-10").json()["items"] == []


# ───────────────────────── comisiones ─────────────────────────
def test_commission_rules_and_payouts(env):
    client, _ = env
    assert client.get("/api/v1/commissions").json()["defaultPct"] == 2.0
    assert client.put("/api/v1/commissions/default-pct", json={"pct": 3.5}).json()["defaultPct"] == 3.5
    assert client.put("/api/v1/commissions/default-pct", json={"pct": 101}).status_code == 400
    out = client.put("/api/v1/commissions/seller-pct", json={"seller": "Ana Pérez", "pct": 5}).json()
    assert out["bySeller"] == {"Ana Pérez": 5.0}  # el nombre del vendedor no se altera
    assert client.put("/api/v1/commissions/seller-pct", json={"seller": "Ana Pérez", "pct": None}).json()["bySeller"] == {}

    p = {"seller": "Ana Pérez", "period": "2026-10", "amount": 120.5}
    out = client.post("/api/v1/commissions/payouts", json=p).json()
    assert out["payouts"][0]["paidByName"] == "Ana Admin"
    client.post("/api/v1/commissions/payouts", json={**p, "amount": 200})  # reemplaza, no duplica
    payouts = client.get("/api/v1/commissions").json()["payouts"]
    assert len(payouts) == 1 and payouts[0]["amount"] == 200


# ───────────────────────── inventario físico ─────────────────────────
def test_stock_count_flow(env):
    client, _ = env
    assert client.post("/api/v1/stock-counts", json={"warehouseName": "No existe"}).status_code == 404
    c = client.post("/api/v1/stock-counts", json={"warehouseName": "Central"})
    assert c.status_code == 201
    count = c.json()
    assert count["code"].startswith("CF-") and count["warehouse"] == "Central"
    assert [i["sku"] for i in count["items"]] == ["ARE", "CEM"]  # ordenado por nombre
    assert count["items"][0]["unitCost"] == 2 and count["summary"]["pending"] == 2

    cid = count["id"]
    closed_early = client.post(f"/api/v1/stock-counts/{cid}/close")
    assert closed_early.status_code == 409 and closed_early.json()["code"] == "PENDING_ITEMS"

    are, cem = count["items"]
    assert client.patch(f"/api/v1/stock-counts/{cid}/items/{are['id']}", json={"counted": 4}).json()["countedQty"] == 4
    client.patch(f"/api/v1/stock-counts/{cid}/items/{cem['id']}", json={"counted": 11})
    assert client.patch(f"/api/v1/stock-counts/{cid}/items/{cem['id']}", json={"counted": -1}).status_code == 400

    detail = client.get(f"/api/v1/stock-counts/{cid}").json()["summary"]
    assert detail == {"itemCount": 2, "pending": 0, "withDiff": 2, "missingValue": 2.0, "surplusValue": 10.0, "net": 8.0}

    done = client.post(f"/api/v1/stock-counts/{cid}/close").json()
    assert done["status"] == "CLOSED" and done["closedAt"]
    again = client.patch(f"/api/v1/stock-counts/{cid}/items/{are['id']}", json={"counted": 1})
    assert again.status_code == 409 and again.json()["code"] == "COUNT_NOT_OPEN"
    listed = client.get("/api/v1/stock-counts").json()["items"]
    assert listed[0]["summary"]["net"] == 8.0 and "items" not in listed[0]


# ───────────────────────── hoja de ruta ─────────────────────────
def test_delivery_confirmation(env):
    client, _ = env
    assert client.put("/api/v1/deliveries/OV-9", json={"status": "DELIVERED", "receiver": "Juan"}).status_code == 404
    not_dispatched = client.put("/api/v1/deliveries/OV-2", json={"status": "DELIVERED", "receiver": "Juan"})
    assert not_dispatched.status_code == 409 and not_dispatched.json()["code"] == "ORDER_NOT_DISPATCHED"
    assert client.put("/api/v1/deliveries/OV-1", json={"status": "DELIVERED"}).status_code == 400  # falta quién recibió
    assert client.put("/api/v1/deliveries/OV-1", json={"status": "NOT_DELIVERED"}).status_code == 400  # falta el motivo

    ok = client.put("/api/v1/deliveries/OV-1", json={"status": "DELIVERED", "receiver": "Juan", "reason": "ignorado"}).json()
    assert ok["orderCode"] == "OV-1" and ok["receiver"] == "Juan" and ok["reason"] is None and ok["confirmedBy"] == "Ana Admin"
    failed = client.put("/api/v1/deliveries/OV-1", json={"status": "NOT_DELIVERED", "reason": "Cliente ausente"}).json()
    assert failed["status"] == "NOT_DELIVERED" and failed["receiver"] is None

    items = client.get("/api/v1/deliveries?codes=OV-1,OV-2,OV-1").json()["items"]
    assert [(i["orderCode"], i["status"]) for i in items] == [("OV-1", "NOT_DELIVERED")]
    assert client.get("/api/v1/deliveries").json()["items"] == []
    assert client.delete("/api/v1/deliveries/OV-1").status_code == 204
    assert client.get("/api/v1/deliveries?codes=OV-1").json()["items"] == []


# ───────────────────────── flujo de caja ─────────────────────────
def test_cashflow_opening_balance(env):
    client, fake = env
    fake.tables["cashflow_settings"] = [{"id": 1, "opening_balance": None}]
    assert client.get("/api/v1/cashflow/settings").json() == {"openingBalance": None}
    assert client.put("/api/v1/cashflow/settings", json={"openingBalance": 15000.5}).json() == {"openingBalance": 15000.5}
    assert client.put("/api/v1/cashflow/settings", json={"openingBalance": None}).json() == {"openingBalance": None}