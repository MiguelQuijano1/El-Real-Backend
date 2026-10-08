from datetime import date
from decimal import Decimal
from typing import Any

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


class CompleteStepIn(ApiModel):
    """Avanza el siguiente paso de la orden con los valores del formulario del frontend."""
    values: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None
    generated_reference: str | None = None


class CancelOrderIn(ApiModel):
    reason: str | None = None