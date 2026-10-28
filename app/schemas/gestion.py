from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.schemas.base import ApiModel

PERIOD = r"^\d{4}-(0[1-9]|1[0-2])$"


class SwitchIn(ApiModel):
    enabled: bool


# ---- Metas ----
class GoalCreate(ApiModel):
    period: str = Field(pattern=PERIOD)
    kind: Literal["vendedor", "categoria"]
    target: str = Field(min_length=1, max_length=160)
    amount: Decimal = Field(gt=0, lt=Decimal("1e11"))


class GoalUpdate(ApiModel):
    kind: Literal["vendedor", "categoria"] | None = None
    target: str | None = Field(default=None, min_length=1, max_length=160)
    amount: Decimal | None = Field(default=None, gt=0, lt=Decimal("1e11"))


class GoalCopy(ApiModel):
    from_period: str = Field(pattern=PERIOD)
    to_period: str = Field(pattern=PERIOD)


# ---- Comisiones ----
class DefaultPctIn(ApiModel):
    pct: Decimal = Field(ge=0, le=100)


class SellerPctIn(ApiModel):
    seller: str = Field(min_length=1, max_length=160)
    pct: Decimal | None = Field(default=None, ge=0, le=100)  # null = vuelve a usar el % general


class PayoutIn(ApiModel):
    seller: str = Field(min_length=1, max_length=160)
    period: str = Field(pattern=PERIOD)
    amount: Decimal = Field(ge=0, lt=Decimal("1e11"))


# ---- Inventario físico ----
class CountCreate(ApiModel):
    warehouse_name: str = Field(min_length=1, max_length=120)


class CountedIn(ApiModel):
    counted: Decimal | None = Field(default=None, ge=0, lt=Decimal("1e9"))  # null = aún sin contar


# ---- Hoja de ruta ----
class DeliveryIn(ApiModel):
    status: Literal["DELIVERED", "NOT_DELIVERED"]
    receiver: str | None = Field(default=None, max_length=160)
    reason: str | None = Field(default=None, max_length=160)
    note: str | None = Field(default=None, max_length=1000)


# ---- Flujo de caja ----
class CashflowIn(ApiModel):
    opening_balance: Decimal | None = Field(default=None, gt=Decimal("-1e11"), lt=Decimal("1e11"))