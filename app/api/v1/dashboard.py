from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.security.deps import AuthUser, require_permission
from app.services import dashboard as dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
def get_dashboard(
    period: Annotated[Literal["today", "week", "month", "year"], Query()] = "month",
    _: AuthUser = Depends(require_permission("dashboard", "view")),
):
    """Ventas, compras, utilidad, stock crítico, cuentas por cobrar/pagar, rotación, últimas ventas y alertas."""
    return dashboard_service.summary(period)