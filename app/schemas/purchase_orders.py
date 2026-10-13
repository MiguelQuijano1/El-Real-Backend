from decimal import Decimal

from pydantic import Field

from app.schemas.base import ApiModel
from app.schemas.orders import OrderLineIn, CompleteStepIn, CancelOrderIn


class PurchaseOrderCreate(ApiModel):
    supplier_id: str
    warehouse_id: str
    seller_user_id: str | None = None
    payment_terms_days: int = Field(default=0, ge=0, le=365)
    tax_rate: Decimal = Field(default=Decimal("0.18"), ge=0, le=1)
    lines: list[OrderLineIn] = Field(min_length=1)
    party_name: str | None = None
    party_tax_id: str | None = None
    party_address: str | None = None
    party_contact: str | None = None
    party_email: str | None = None
    origin: str | None = None

class ReceiptLineIn(ApiModel):
    sku: str = Field(min_length=1, max_length=64)
    quantity: Decimal = Field(ge=0)


class ApproveReceiptIn(ApiModel):
    """Aprobación de la recepción en Kardex: recepción + factura (con archivo) + pago, en una sola operación."""
    lines: list[ReceiptLineIn] = Field(default_factory=list)
    received_at: str | None = None
    supplier_guide: str | None = Field(default=None, max_length=60)
    warehouse: str | None = None
    observations: str | None = Field(default=None, max_length=500)
    invoice_type: str = Field(default="Factura", max_length=30)  # Factura | Boleta de venta | Nota de crédito | …
    invoice_number: str = Field(min_length=1, max_length=40)
    invoice_issued_at: str | None = None
    invoice_due_at: str | None = None
    invoice_total: Decimal | None = Field(default=None, ge=0)
    document_id: str | None = None  # archivo del comprobante (opcional)
    pay_now: bool | None = None
    payment_channel: str = Field(default="CASH")  # CASH | BANK
    bank_account_id: str | None = None
    payment_reference: str | None = Field(default=None, max_length=80)
