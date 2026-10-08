from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import Field

from app.schemas.base import ApiModel
from app.schemas.orders import OrderLineIn


class QuotationCreate(ApiModel):
    customer_id: str
    seller_user_id: str | None = None
    warehouse_id: str | None = None
    valid_until: date | None = None
    payment_terms_days: int = Field(default=0, ge=0, le=365)
    lines: list[OrderLineIn] = Field(min_length=1)
    status: str | None = Field(default="DRAFT", pattern="^(DRAFT|SENT)$")


class QuotationUpdate(ApiModel):
    customer_id: str | None = None
    seller_user_id: str | None = None
    warehouse_id: str | None = None
    valid_until: date | None = None
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    lines: list[OrderLineIn] | None = None
    status: str | None = Field(default=None, pattern="^(DRAFT|SENT|APPROVED|REJECTED)$")
    rejected_reason: str | None = None


class QuotationStatusIn(ApiModel):
    status: str = Field(pattern="^(DRAFT|SENT|APPROVED|REJECTED)$")
    rejected_reason: str | None = None