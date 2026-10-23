"""Descuento global de la orden de venta: se valida contra el máximo de Configuración."""
from app.services.orders import discount_error


def test_dentro_del_maximo():
    # 100 de subtotal, 5 de descuento → neto 95: 5 %
    assert discount_error(95, 5, 5) is None


def test_supera_el_maximo():
    assert "supera" in discount_error(90, 10, 5)


def test_orden_sin_importe():
    assert discount_error(0, 0.5, 100) is not None