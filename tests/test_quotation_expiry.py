"""Vencimiento de cotizaciones: se deriva de `valid_until`, sin proceso programado."""
from datetime import date

import pytest

from app.core.errors import ApiError
from app.services import quotations as svc


def _row(status: str, valid_until: str | None) -> dict:
    return {"id": "q1", "code": "COT-2026-0001", "status": status, "valid_until": valid_until, "created_at": "2026-10-01T00:00:00Z", "lines": []}


def test_la_fecha_indicada_aun_es_valida_y_vence_al_dia_siguiente():
    hoy = date(2026, 10, 13)
    assert svc.is_expired("SENT", "2026-10-13", hoy) is False
    assert svc.is_expired("SENT", "2026-10-12", hoy) is True


@pytest.mark.parametrize("status", ["DRAFT", "SENT", "APPROVED"])
def test_cotizaciones_abiertas_vencen(status):
    assert svc.is_expired(status, "2020-01-01") is True


@pytest.mark.parametrize("status", ["CONVERTED", "REJECTED"])
def test_convertida_o_rechazada_no_cambian_a_vencida(status):
    assert svc.is_expired(status, "2020-01-01") is False


def test_sin_fecha_nunca_vence():
    assert svc.is_expired("SENT", None) is False


def test_serializa_como_vencida_y_conserva_el_estado_guardado_para_filtros():
    out = svc._serialize(_row("APPROVED", "2020-01-01"))
    assert out["status"] == "Vencida"
    assert out["statusKey"] == "EXPIRED"
    vigente = svc._serialize(_row("APPROVED", "2999-01-01"))
    assert vigente["status"] == "Aprobada"
    assert vigente["statusKey"] == "APPROVED"


def test_no_se_puede_convertir_una_cotizacion_vencida(monkeypatch):
    monkeypatch.setattr(svc, "get_quotation", lambda _id: svc._serialize(_row("APPROVED", "2020-01-01")))
    with pytest.raises(ApiError) as e:
        svc.convert_quotation("COT-2026-0001", actor=None, meta=None)
    assert "venció" in str(e.value.message)


def test_cotizacion_sin_cliente():
    out = svc._serialize(_row("DRAFT", "2999-01-01") | {"customer_id": None})
    assert out["prospect"] is True
    assert out["party"] == "Sin cliente"
    assert out["data"]["cliente"] == ""


def test_convertir_sin_cliente_exige_elegirlo(monkeypatch):
    monkeypatch.setattr(svc, "get_quotation", lambda _id: svc._serialize(_row("APPROVED", "2999-01-01") | {"customer_id": None}))
    monkeypatch.setattr(svc, "first", lambda _q: {"id": "q1", "customer_id": None})
    monkeypatch.setattr(svc, "db", lambda: type("D", (), {"table": lambda *_a: type("T", (), {"select": lambda *_b: type("S", (), {"eq": lambda *_c: None})()})()})())
    with pytest.raises(ApiError) as e:
        svc.convert_quotation("COT-2026-0001", actor=None, meta=None)
    assert "no tiene cliente" in e.value.message
