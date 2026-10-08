from datetime import date
from decimal import Decimal

from pydantic import Field

from app.schemas.base import ApiModel


class RequestLineIn(ApiModel):
    product_id: str | None = None
    sku: str | None = None
    name: str = Field(min_length=1, max_length=200)
    unit: str = Field(min_length=1, max_length=24)
    quantity: Decimal = Field(gt=0)
    estimated_unit_cost: Decimal = Field(ge=0)


class PurchaseRequestCreate(ApiModel):
    requesting_area: str = Field(min_length=1, max_length=60)
    reason: str = Field(min_length=1)
    priority: str = Field(default="MEDIUM", pattern="^(HIGH|MEDIUM|LOW)$")
    required_by: date | None = None
    warehouse_id: str | None = None
    suggested_supplier_id: str | None = None
    lines: list[RequestLineIn] = Field(min_length=1)


class PurchaseRequestUpdate(ApiModel):
    requesting_area: str | None = Field(default=None, max_length=60)
    reason: str | None = None
    priority: str | None = Field(default=None, pattern="^(HIGH|MEDIUM|LOW)$")
    required_by: date | None = None
    warehouse_id: str | None = None
    suggested_supplier_id: str | None = None
    lines: list[RequestLineIn] | None = None


class PurchaseRequestStatusIn(ApiModel):
    status: str = Field(pattern="^(PENDING|APPROVED|REJECTED)$")
    rejection_reason: str | None = None


class ConvertRequestIn(ApiModel):
    supplier_id: str
    warehouse_id: str | None = None
    payment_terms_days: int = Field(default=0, ge=0, le=365)