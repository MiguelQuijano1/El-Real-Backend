from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiModel


class UpdateSettingsIn(ApiModel):
    tax_id: str | None = Field(default=None, max_length=24)
    legal_name: str | None = Field(default=None, max_length=200)
    fiscal_address: str | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    time_zone: str | None = Field(default=None, max_length=64)
    invoice_series: str | None = Field(default=None, max_length=12)
    receipt_series: str | None = Field(default=None, max_length=12)
    dispatch_series: str | None = Field(default=None, max_length=12)
    credit_note_series: str | None = Field(default=None, max_length=12)
    auto_tax_submission: bool | None = None
    email_documents: bool | None = None
    tax_rate: Decimal | None = Field(default=None, ge=0, le=1)
    prices_include_tax: bool | None = None
    max_discount_pct: Decimal | None = Field(default=None, ge=0, le=100)
    quotation_validity_days: int | None = Field(default=None, ge=1, le=365)
    cost_method: Literal["WEIGHTED_AVERAGE"] | None = None
    default_warehouse_id: UUID | None = None
    allow_negative_stock: bool | None = None
    reorder_alerts_enabled: bool | None = None
    session_duration_hours: int | None = Field(default=None, ge=1, le=72)
    max_login_attempts: int | None = Field(default=None, ge=3, le=20)
    mfa_required: bool | None = None
    audit_retention_years: int | None = Field(default=None, ge=1, le=20)
