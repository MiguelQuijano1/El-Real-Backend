from decimal import Decimal

from pydantic import Field

from app.schemas.base import ApiModel


class CollectIn(ApiModel):
    amount: Decimal = Field(gt=0)
    channel: str = Field(default="CASH")  # CASH | BANK
    bank_account_id: str | None = None
    payment_method: str | None = None
    reference: str | None = None
    description: str | None = None


class PayIn(ApiModel):
    amount: Decimal = Field(gt=0)
    channel: str = Field(default="BANK")
    bank_account_id: str | None = None
    payment_method: str | None = None
    reference: str | None = None
    description: str | None = None


class CashMovementIn(ApiModel):
    amount: Decimal = Field(gt=0)
    tipo: str = Field(default="Ingreso")  # Ingreso | Egreso
    description: str = Field(min_length=1)
    reference: str | None = None
    movement_type: str | None = None


class BankMovementIn(ApiModel):
    amount: Decimal = Field(gt=0)
    tipo: str = Field(default="Abono")  # Abono | Cargo
    description: str = Field(min_length=1)
    bank_account_id: str | None = None
    cuenta: str | None = None
    reference: str | None = None


class ReconcileIn(ApiModel):
    reference: str | None = None