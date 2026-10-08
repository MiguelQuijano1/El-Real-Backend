import pytest

from app.core.config import duration_to_seconds
from app.core.rate_limit import SlidingWindowLimiter
from app.core.serialization import camel, to_camel
from app.security.catalog import ALL_MODULE_KEYS, PERMISSION_ACTIONS
from app.security.passwords import hash_password, verify_password
from app.security.tokens import create_access_token, decode_access_token, hash_token


def test_password_roundtrip_uses_argon2id():
    h = hash_password("una-clave-larga-123")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "una-clave-larga-123")
    assert not verify_password(h, "otra")
    assert not verify_password("no-es-un-hash", "x")


def test_token_roundtrip_and_tamper():
    token, _ = create_access_token("user-1", "sess-1")
    assert decode_access_token(token)["sub"] == "user-1"
    assert decode_access_token(token + "x") is None
    assert hash_token(token) != token


def test_duration():
    assert duration_to_seconds("8h") == 28800
    with pytest.raises(ValueError):
        duration_to_seconds("8 horas")


def test_camel():
    assert to_camel("role_type") == "roleType"
    assert camel([{"full_name": "x", "role": {"is_active": True}}]) == [{"fullName": "x", "role": {"isActive": True}}]


def test_rate_limiter_window():
    lim = SlidingWindowLimiter()
    assert all(lim.hit("k", 3, 60) for _ in range(3))
    assert not lim.hit("k", 3, 60)
    assert lim.hit("otra", 3, 60)


def test_catalog_has_no_duplicates():
    assert len(ALL_MODULE_KEYS) == len(set(ALL_MODULE_KEYS)) == 29
    assert PERMISSION_ACTIONS == ["view", "create", "edit", "delete", "approve"]


class _FakeQuery:
    def __init__(self, calls):
        self.calls = calls

    def select(self, cols):
        self.calls.append(("select", cols))
        return self

    def eq(self, col, value):
        self.calls.append(("eq", col, value))
        return self

    def limit(self, n):
        return self

    def execute(self):
        class _R:
            data = [{"id": "x"}]

        return _R()


class _FakeDb:
    def __init__(self):
        self.calls = []

    def table(self, name):
        self.calls.append(("table", name))
        return _FakeQuery(self.calls)


@pytest.fixture
def fake_db():
    from app.repositories.client import set_client

    fake = _FakeDb()
    set_client(fake)
    yield fake
    set_client(None)


def test_find_by_id_or_code_uses_code_column_for_readable_codes(fake_db):
    from app.repositories.client import find_by_id_or_code

    assert find_by_id_or_code("business_orders", "OV-2026-0001", "*", kind="venta") == {"id": "x"}
    # Un código nunca debe compararse con la columna uuid `id` (Postgres lo rechaza con 22P02).
    assert ("eq", "code", "OV-2026-0001") in fake_db.calls
    assert ("eq", "kind", "venta") in fake_db.calls
    assert not any(c[:2] == ("eq", "id") for c in fake_db.calls)


def test_find_by_id_or_code_uses_id_column_for_uuids(fake_db):
    from app.repositories.client import find_by_id_or_code

    uid = "9e70c16b-414a-4d76-a51a-4c7509bff86b"
    find_by_id_or_code("quotations", uid, "id, code")
    assert ("eq", "id", uid) in fake_db.calls
    assert ("select", "id, code") in fake_db.calls
    assert not any(c[:2] == ("eq", "code") for c in fake_db.calls)


def test_postgrest_client_does_not_share_one_http2_connection():
    """postgrest crea su cliente httpx con http2=True: una sola conexión compartida entre hilos que, bajo
    concurrencia, falla con httpx.ReadError y la API responde 500 (sin cabeceras CORS)."""
    from postgrest import SyncPostgrestClient

    from app.repositories.client import use_http1

    pg = SyncPostgrestClient("http://localhost:3001/rest/v1", headers={"apikey": "k", "Authorization": "Bearer k"})
    assert pg.session._transport._pool._http2  # comportamiento por defecto de la librería
    use_http1(pg)
    assert not pg.session._transport._pool._http2
    assert str(pg.session.base_url).startswith("http://localhost:3001/rest/v1")
    assert pg.session.headers["apikey"] == "k"


class _Res:
    def __init__(self, data):
        self.data = data


class _OrdersFakeDb:
    """Cliente falso mínimo para `_create_sales_invoice`: líneas, ajustes y un insert que puede fallar."""

    def __init__(self, fail_insert_on=None):
        self.inserted = []
        self.fail_insert_on = fail_insert_on
        self._table = None
        self._op = None
        self._payload = None

    def table(self, name):
        self._table, self._op, self._payload = name, "select", None
        return self

    def select(self, *_a):
        return self

    def eq(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, _n):
        return self

    def insert(self, row):
        self._op, self._payload = "insert", row
        return self

    def execute(self):
        if self._op == "insert":
            if self._table == self.fail_insert_on:
                from postgrest.exceptions import APIError

                raise APIError({"code": "23505", "message": "duplicate key"})
            self.inserted.append((self._table, self._payload))
            return _Res([{"id": "new-id"}])
        if self._table == "order_lines":
            return _Res([{"quantity": 2, "unit_price": 10}])
        if self._table == "company_settings":
            return _Res([{"invoice_series": "F001", "receipt_series": "B001"}])
        return _Res([])


_ORDER = {"id": "o1", "customer_id": "c1", "tax_rate": 0.18, "code": "OV-1"}


def test_sales_invoice_failure_is_not_swallowed():
    from app.core.errors import ApiError
    from app.repositories.client import set_client
    from app.services import orders

    set_client(_OrdersFakeDb(fail_insert_on="sales_invoices"))
    try:
        with pytest.raises(ApiError):
            orders._create_sales_invoice(_ORDER, {}, None)
    finally:
        set_client(None)


def test_sales_invoice_understands_the_frontend_field_names():
    from app.repositories.client import set_client
    from app.services import orders

    fake = _OrdersFakeDb()
    set_client(fake)
    try:
        orders._create_sales_invoice(_ORDER, {"tipo": "Boleta de venta electrónica", "ser": "B001"}, None)
        orders._create_sales_invoice(_ORDER, {"tipo": "Factura electrónica", "ser": "F001"}, None)
    finally:
        set_client(None)
    boleta, factura = (row for _t, row in fake.inserted)
    assert (boleta["document_type"], boleta["series"]) == ("RECEIPT", "B001")
    assert (factura["document_type"], factura["series"]) == ("INVOICE", "F001")


@pytest.mark.parametrize(
    "stock, avg, qty, cost, expected",
    [
        (0, 0, 10, 8, 8),  # primera entrada: el costo de la entrada
        (10, 8, 10, 12, 10),  # (10*8 + 10*12) / 20
        (10, 8, 5, 8, 8),  # mismo costo: no cambia
        (-3, 8, 10, 12, 12),  # stock negativo: no se mezcla con deuda de stock
        (11, 0, 5, 6, 6),  # stock con costo desconocido (datos previos al cálculo): toma el costo de la entrada
        (10, 8, 0, 99, 8),  # entrada de cantidad 0: sin cambios
    ],
)
def test_weighted_average_cost(stock, avg, qty, cost, expected):
    from app.services.costing import weighted_average

    assert weighted_average(stock, avg, qty, cost) == pytest.approx(expected)
