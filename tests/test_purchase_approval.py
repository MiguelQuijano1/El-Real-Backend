"""Aprobación de la recepción de una orden de compra (Kardex): recepción + factura + egreso, de forma atómica.

Se ejecuta contra un cliente en memoria (no necesita Supabase) que imita lo que usa la capa de servicios:
table().select/insert/update/delete + eq/in_/is_/order/range/limit + rpc().
"""
import itertools
import uuid

import pytest

from app.core.errors import ApiError
from app.repositories import client as repo
from app.security.deps import AuthUser
from app.services import purchase_orders as svc
from app.services.audit import RequestMeta


class _Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count if count is not None else len(data)


class _Query:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.mode, self.payload, self.filters = "select", None, []

    def select(self, *_a, **_k):
        return self

    def insert(self, payload):
        self.mode, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.mode, self.payload = "update", payload
        return self

    def delete(self):
        self.mode = "delete"
        return self

    def eq(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) == v)
        return self

    def in_(self, col, vals):
        self.filters.append(lambda r, c=col, v=list(vals): r.get(c) in v)
        return self

    def is_(self, col, val):
        self.filters.append(lambda r, c=col: r.get(c) is None)
        return self

    def gte(self, col, val):
        self.filters.append(lambda r, c=col, v=val: (r.get(c) or 0) >= v)
        return self

    def lt(self, col, val):
        self.filters.append(lambda r, c=col, v=val: (r.get(c) or 0) < v)
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        db = self.db
        table = db.tables.setdefault(self.name, [])
        db.log.append((self.name, self.mode))
        if (self.name, self.mode) in db.fail_on:
            raise RuntimeError(f"fallo simulado en {self.name}.{self.mode}")
        if self.mode == "insert":
            payloads = self.payload if isinstance(self.payload, list) else [self.payload]
            created = []
            for p in payloads:
                row = {"id": str(uuid.uuid4()), **p}
                table.append(row)
                created.append(dict(row))
            return _Result(created)
        match = [r for r in table if all(f(r) for f in self.filters)]
        if self.mode == "update":
            for r in match:
                r.update(self.payload)
            return _Result([dict(r) for r in match])
        if self.mode == "delete":
            for r in match:
                table.remove(r)
            return _Result([dict(r) for r in match])
        return _Result([dict(r) for r in match])


class FakeDB:
    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self.log: list[tuple[str, str]] = []
        self.fail_on: set[tuple[str, str]] = set()
        self._seq = itertools.count(1)

    def table(self, name):
        return _Query(self, name)

    def rpc(self, _name, _params):
        n = next(self._seq)

        class _R:
            def execute(self_inner):
                return _Result([n])

        return _R()

    # --- helpers de prueba
    def rows(self, name, **where):
        return [r for r in self.tables.get(name, []) if all(r.get(k) == v for k, v in where.items())]


@pytest.fixture
def world(monkeypatch):
    db = FakeDB()
    repo.set_client(db)
    db.tables["suppliers"] = [{"id": "s1", "legal_name": "Proveedor SAC", "tax_id": "20123456789", "payment_terms_days": 0}]
    db.tables["warehouses"] = [{"id": "w1", "name": "Almacén principal", "status": "ACTIVE"}]
    db.tables["products"] = [
        {"id": "p1", "sku": "A-1", "average_cost": 0},
        {"id": "p2", "sku": "B-2", "average_cost": 0},
    ]
    db.tables["company_settings"] = [{"id": 1, "tax_rate": 0.18}]
    db.tables["documents"] = [{"id": "d1", "name": "factura.pdf", "deleted_at": None}]
    # Caja con S/ 5,000 de saldo
    db.tables["treasury_movements"] = [{"id": "t0", "channel": "CASH", "movement_type": "OPENING", "signed_amount": 5000}]
    db.tables["business_orders"] = [{
        "id": "o1", "code": "OC-2026-0001", "kind": "PURCHASE", "supplier_id": "s1", "warehouse_id": "w1",
        "party_name": "Proveedor SAC", "tax_rate": 0.18, "payment_terms_days": 0, "current_step": 2, "is_cancelled": False,
    }]
    db.tables["order_lines"] = [
        {"id": "l1", "order_id": "o1", "line_no": 1, "product_id": "p1", "sku_snapshot": "A-1", "name_snapshot": "Producto A", "unit_snapshot": "und", "quantity": 10, "unit_price": 20},
        {"id": "l2", "order_id": "o1", "line_no": 2, "product_id": "p2", "sku_snapshot": "B-2", "name_snapshot": "Producto B", "unit_snapshot": "und", "quantity": 5, "unit_price": 40},
    ]
    # El serializador real usa embebidos de PostgREST que el cliente en memoria no resuelve.
    monkeypatch.setattr(svc, "get_purchase_order", lambda oid: dict(db.rows("business_orders", id=oid)[0]))
    yield db
    repo.set_client(None)


ACTOR = AuthUser(id="u1", role_id="r1", full_name="Ana Admin", email="a@x.pe", area=None, status="ACTIVE",
                 last_login_at=None, role={"code": "ADMIN", "name": "Administrador"}, session_id="s")


def payload(**over):
    base = {
        "invoice_number": "F002-00001", "document_id": "d1", "invoice_issued_at": "2026-10-12", "invoice_due_at": "2026-10-12",
        "supplier_guide": "EG07-1", "payment_channel": "CASH",
    }
    return {**base, **over}


def cash(db):
    return sum(float(m["signed_amount"]) for m in db.rows("treasury_movements", channel="CASH"))


# total de la orden = (10*20 + 5*40) * 1.18 = 472.00
TOTAL = 472.0


def test_create_does_not_ask_for_approval_nor_touch_stock(world):
    out = svc.create_purchase_order(
        {"supplier_id": "s1", "warehouse_id": "w1", "lines": [
            {"product_id": "p1", "sku": "A-1", "name": "Producto A", "unit": "und", "quantity": 3, "unit_price": 10}]},
        ACTOR, RequestMeta(),
    )
    created = next(o for o in world.rows("business_orders") if o["id"] == out["id"])
    assert created["current_step"] == 2  # lista para recepción: sin paso de aprobación
    assert world.rows("inventory_movements") == []  # crear no mueve inventario
    assert [e["step_no"] for e in world.rows("order_step_events", order_id=created["id"])] == [0, 1]


def test_approval_posts_receipt_invoice_and_cash_egress_once(world):
    svc.approve_receipt("OC-2026-0001", payload(), ACTOR, RequestMeta())

    order = world.rows("business_orders", id="o1")[0]
    assert order["current_step"] == 5
    moves = world.rows("inventory_movements")
    assert sorted((m["quantity_delta"], m["movement_type"]) for m in moves) == [(5.0, "RECEIPT"), (10.0, "RECEIPT")]
    inv = world.rows("supplier_invoices")
    assert len(inv) == 1 and inv[0]["total"] == TOTAL and inv[0]["validation_status"] == "MATCHES_ORDER_RECEIPT"
    pays = [m for m in world.rows("treasury_movements") if m["movement_type"] == "PAYMENT"]
    assert len(pays) == 1 and pays[0]["signed_amount"] == -TOTAL and pays[0]["supplier_invoice_id"] == inv[0]["id"]
    assert cash(world) == 5000 - TOTAL
    assert [e["step_no"] for e in world.rows("order_step_events") if e["step_no"] > 1] == [2, 3, 4]
    assert world.rows("documents", id="d1")[0]["related_order_id"] == "o1"
    assert {p["average_cost"] for p in world.rows("products")} == {20.0, 40.0}


def test_repeating_the_approval_does_not_duplicate_anything(world):
    svc.approve_receipt("OC-2026-0001", payload(), ACTOR, RequestMeta())
    with pytest.raises(ApiError) as e:
        svc.approve_receipt("OC-2026-0001", payload(invoice_number="F002-00002"), ACTOR, RequestMeta())
    assert e.value.status_code == 409
    assert len(world.rows("inventory_movements")) == 2
    assert len([m for m in world.rows("treasury_movements") if m["movement_type"] == "PAYMENT"]) == 1
    assert len(world.rows("supplier_invoices")) == 1
    assert cash(world) == 5000 - TOTAL


def test_insufficient_cash_writes_nothing(world):
    world.tables["treasury_movements"][0]["signed_amount"] = 100
    with pytest.raises(ApiError) as e:
        svc.approve_receipt("OC-2026-0001", payload(), ACTOR, RequestMeta())
    assert e.value.status_code == 400
    assert world.rows("business_orders", id="o1")[0]["current_step"] == 2
    assert world.rows("goods_receipts") == [] and world.rows("inventory_movements") == []
    assert world.rows("supplier_invoices") == []
    assert cash(world) == 100


def test_failure_after_receipt_rolls_everything_back(world):
    world.fail_on.add(("treasury_movements", "insert"))  # el pago falla al final
    with pytest.raises(RuntimeError):
        svc.approve_receipt("OC-2026-0001", payload(), ACTOR, RequestMeta())
    assert world.rows("business_orders", id="o1")[0]["current_step"] == 2
    assert world.rows("goods_receipts") == []
    assert world.rows("inventory_movements") == []
    assert world.rows("supplier_invoices") == []
    assert {p["average_cost"] for p in world.rows("products")} == {0}  # costos promedio restituidos
    assert [e["step_no"] for e in world.rows("order_step_events")] == []
    assert cash(world) == 5000

    # y se puede reintentar sin residuos
    world.fail_on.clear()
    svc.approve_receipt("OC-2026-0001", payload(), ACTOR, RequestMeta())
    assert world.rows("business_orders", id="o1")[0]["current_step"] == 5
    assert len(world.rows("inventory_movements")) == 2


def test_credit_terms_register_invoice_but_do_not_pay(world):
    world.tables["business_orders"][0]["payment_terms_days"] = 30
    svc.approve_receipt("OC-2026-0001", payload(), ACTOR, RequestMeta())
    assert world.rows("business_orders", id="o1")[0]["current_step"] == 4  # factura registrada, pago pendiente
    assert len(world.rows("supplier_invoices")) == 1
    assert [m for m in world.rows("treasury_movements") if m["movement_type"] == "PAYMENT"] == []
    assert cash(world) == 5000


def test_received_quantities_are_validated_and_difference_is_flagged(world):
    with pytest.raises(ApiError):
        svc.approve_receipt("OC-2026-0001", payload(lines=[{"sku": "A-1", "quantity": 11}]), ACTOR, RequestMeta())
    assert world.rows("inventory_movements") == []

    svc.approve_receipt("OC-2026-0001", payload(lines=[{"sku": "A-1", "quantity": 8}], invoice_total=None), ACTOR, RequestMeta())
    qty = {m["order_line_id"]: m["quantity_delta"] for m in world.rows("inventory_movements")}
    assert qty == {"l1": 8.0, "l2": 5.0}
    assert world.rows("goods_receipts")[0]["condition"] == "OBSERVATIONS"
    inv = world.rows("supplier_invoices")[0]
    assert inv["validation_status"] == "HAS_DIFFERENCES"
    assert inv["total"] == round((8 * 20 + 5 * 40) * 1.18, 2)  # factura por lo realmente recibido


def test_invoice_file_is_optional_but_number_is_required(world):
    with pytest.raises(ApiError):
        svc.approve_receipt("OC-2026-0001", payload(document_id="no-existe"), ACTOR, RequestMeta())
    with pytest.raises(ApiError):
        svc.approve_receipt("OC-2026-0001", payload(invoice_number="  "), ACTOR, RequestMeta())
    assert world.rows("business_orders", id="o1")[0]["current_step"] == 2


def test_legacy_order_waiting_for_approval_can_be_received_directly(world):
    world.tables["business_orders"][0]["current_step"] = 1  # creada con el flujo anterior («Por aprobar»)
    svc.approve_receipt("OC-2026-0001", payload(), ACTOR, RequestMeta())
    assert world.rows("business_orders", id="o1")[0]["current_step"] == 5
    assert len(world.rows("inventory_movements")) == 2


def test_invoice_type_is_kept_and_due_date_follows_payment_terms(world):
    world.tables["business_orders"][0]["payment_terms_days"] = 30
    svc.approve_receipt(
        "OC-2026-0001",
        {"invoice_type": "Boleta de venta", "invoice_number": "B001-77", "document_id": "d1", "received_at": "2026-10-12"},
        ACTOR, RequestMeta(),
    )
    inv = world.rows("supplier_invoices")[0]
    assert inv["issued_at"] == "2026-10-12" and inv["due_at"] == "2026-11-11"
    assert inv["total"] == TOTAL  # el monto sale de lo recibido, no se digita
    ev = [e for e in world.rows("order_step_events") if e["step_no"] == 3][0]
    assert ev["generated_reference"] == "Boleta de venta B001-77" and ev["form_data"]["tipo"] == "Boleta de venta"


def test_approval_without_invoice_file(world):
    svc.approve_receipt("OC-2026-0001", payload(document_id=None), ACTOR, RequestMeta())
    assert world.rows("business_orders", id="o1")[0]["current_step"] == 5
    assert len(world.rows("supplier_invoices")) == 1
    assert world.rows("documents", id="d1")[0].get("related_order_id") is None  # sin archivo no se vincula nada
