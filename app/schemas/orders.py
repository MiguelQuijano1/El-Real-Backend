from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from app.schemas.base import ApiModel


class OrderLineIn(ApiModel):
    product_id: str | None = None
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    unit: str = Field(min_length=1, max_length=24)
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)


class SaleOrderCreate(ApiModel):
    customer_id: str
    warehouse_id: str
    seller_user_id: str | None = None
    payment_terms_days: int = Field(default=0, ge=0, le=365)
    tax_rate: Decimal = Field(default=Decimal("0.18"), ge=0, le=1)
    lines: list[OrderLineIn] = Field(min_length=1)
    # Snapshot opcional si el frontend ya tiene los datos del cliente
    party_name: str | None = None
    party_tax_id: str | None = None
    party_address: str | None = None
    party_contact: str | None = None
    party_email: str | None = None
    origin: str | None = None  # cotización u origen legible
    # Datos de entrega elegidos en el modal de venta: se guardan en el evento del paso 0 y precargan confirmación/despacho
    carrier: str | None = Field(default=None, max_length=120)
    # Conductor y vehículo de la flota propia elegidos en el modal; precargan el despacho.
    driver: str | None = Field(default=None, max_length=160)
    vehicle_plate: str | None = Field(default=None, max_length=20)
    delivery_date: str | None = Field(default=None, max_length=10)
    # Venta al contado: fecha límite de pago (yyyy-mm-dd); vencida y sin aprobar, la orden se anula sola.
    pay_deadline: str | None = Field(default=None, max_length=10)
    delivery_address: str | None = Field(default=None, max_length=300)
    # Comprobante que se emitirá al facturar (sales_document_type: INVOICE / RECEIPT). Factura exige RUC.
    document_type: Literal["Factura", "Boleta"] | None = None
    # Descuento global (S/, sin IGV) ya repartido en el precio de cada línea; se guarda para mostrarlo y se valida
    # contra el descuento máximo de Configuración (company_settings.max_discount_pct).
    discount_amount: Decimal = Field(default=Decimal("0"), ge=0)


class CompleteStepIn(ApiModel):
    """Avanza el siguiente paso de la orden con los valores del formulario del frontend."""
    values: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None
    generated_reference: str | None = None


class CancelOrderIn(ApiModel):
    reason: str | None = None