"""Venta al contado: la orden sin aprobar se anula sola al vencer su fecha límite de pago."""
from datetime import date

from app.services import orders as svc


def test_la_fecha_limite_sigue_vigente_ese_dia_y_vence_al_siguiente():
    hoy = date(2026, 10, 13)
    assert svc.is_past_deadline("2026-10-13", hoy) is False
    assert svc.is_past_deadline("2026-10-12", hoy) is True


def test_sin_fecha_limite_nunca_vence():
    assert svc.is_past_deadline(None) is False
    assert svc.is_past_deadline("") is False
    assert svc.is_past_deadline("no-es-fecha") is False


class _Q:
    """Cliente mínimo: encadena filtros y devuelve filas fijas / registra los updates."""

    def __init__(self, store, name):
        self.store, self.name, self._update = store, name, None

    def select(self, *_a, **_k): return self
    def eq(self, *_a): return self
    def lte(self, *_a): return self
    def in_(self, *_a): return self
    def update(self, patch): self._update = patch; return self

    def execute(self):
        if self._update is not None:
            self.store["updates"].append((self.name, self._update))
            return type("R", (), {"data": []})()
        return type("R", (), {"data": self.store[self.name]})()


class _DB:
    def __init__(self, store): self.store = store
    def table(self, name): return _Q(self.store, name)


def test_barrido_anula_solo_las_vencidas(monkeypatch):
    store = {
        "business_orders": [{"id": "a", "code": "OV-1"}, {"id": "b", "code": "OV-2"}, {"id": "c", "code": "OV-3"}],
        "order_step_events": [
            {"order_id": "a", "step_no": 0, "form_data": {"lim": "2026-10-10"}},  # vencida
            {"order_id": "b", "step_no": 0, "form_data": {"lim": "2026-10-20"}},  # vigente
            {"order_id": "c", "step_no": 0, "form_data": {}},  # crédito: sin límite
        ],
        "updates": [],
    }
    monkeypatch.setattr(svc, "db", lambda: _DB(store))
    monkeypatch.setattr(svc, "record", lambda *a, **k: None)
    assert svc.expire_unpaid_orders(date(2026, 10, 13)) == 1
    assert len(store["updates"]) == 1 and store["updates"][0][1]["is_cancelled"] is True


def test_credito_exige_linea_y_saldo_disponible():
    assert "no tiene línea de crédito" in svc.credit_problem(0, 0, 100)
    assert svc.credit_problem(1000, 400, 600) is None  # justo lo disponible
    msg = svc.credit_problem(1000, 400, 700)
    assert msg and "supera el crédito disponible" in msg and "600.00" in msg
    assert svc.credit_problem(1000, 1200, 10) is not None  # ya excedido
