from decimal import Decimal

from pydantic import Field

from app.schemas.base import ApiModel


class TransferLineIn(ApiModel):
    product_id: str
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    unit: str = Field(min_length=1, max_length=24)
    quantity: Decimal = Field(gt=0)


class TransferCreate(ApiModel):
    source_warehouse_id: str
    destination_warehouse_id: str
    responsible_user_id: str | None = None
    lines: list[TransferLineIn] = Field(min_length=1)


class AdjustmentCreate(ApiModel):
    product_id: str
    warehouse_id: str
    adjustment_type: str = Field(pattern="^(LOSS|SURPLUS|EXPIRY|CORRECTION|Merma|Sobrante|Vencimiento|Corrección)$")
    quantity: Decimal = Field(gt=0)
    reason: str = Field(min_length=1)