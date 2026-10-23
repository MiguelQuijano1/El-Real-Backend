"""Descuento global de la orden de venta: se valida contra el máximo de Configuración."""
from app.services.orders import discount_error


def test_dentro_del_maximo():
    # 100 de subtotal, 5 de descuento → neto 95: 5 %
    assert discount_error(95, 5, 5) is None


def test_supera_el_maximo():
    assert "supera" in discount_error(90, 10, 5)


def test_orden_sin_importe():
    assert discount_error(0, 0.5, 100) is not None

def test_cinco_por_ciento_redondeado_al_centimo():
    # 5 % de S/ 18.90 = 0.945 → S/ 0.95: debe aceptarse (antes se bloqueaba por dar 5.03 %)
    assert discount_error(17.95, 0.95, 5) is None
    assert discount_error(17.94, 0.96, 5) is not None


def test_monto_en_soles_sin_tope_de_configuracion():
    # S/ 12 sobre S/ 18.90 (63 %) se permite si se escribió en soles
    assert discount_error(6.90, 12, 5, "amount") is None
    # pero nunca igual o mayor que el subtotal
    assert discount_error(0, 18.90, 5, "amount") is not None