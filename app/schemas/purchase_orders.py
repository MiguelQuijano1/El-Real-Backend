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