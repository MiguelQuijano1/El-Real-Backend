from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, Request

from app.schemas import orders as sch
from app.security.deps import AuthUser, request_meta, require_permission
from app.services import orders as svc

router = APIRouter(prefix="/sales-orders", tags=["sales-orders"])

_view = require_permission("sales-orders", "view")
_create = require_permission("sales-orders", "create")
_edit = require_permission("sales-orders", "edit")


@router.get("", summary="Listar órdenes de venta")
def list_orders(
    q: Annotated[str | None, Query(max_length=100)] = None,
    cancelled: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    _: AuthUser = Depends(_view),
):
    return svc.list_sale_orders(q=q, cancelled=cancelled, limit=limit, offset=offset)


@router.get("/{order_id}", summary="Obtener orden de venta")
def get_order(order_id: str, _: AuthUser = Depends(_view)):
    return svc.get_sale_order(order_id)


@router.post("", status_code=201, summary="Crear orden de venta")
def create_order(
    request: Request,
    payload: sch.SaleOrderCreate = Body(...),
    actor: AuthUser = Depends(_create),
):
    return svc.create_sale_order(payload.model_dump(mode="json"), actor, request_meta(request))


@router.post("/{order_id}/complete-step", summary="Completar siguiente paso")
def complete_step(
    order_id: str,
    request: Request,
    payload: sch.CompleteStepIn = Body(...),
    actor: AuthUser = Depends(_edit),
):
    return svc.complete_step(order_id, payload.model_dump(mode="json"), actor, request_meta(request))


@router.post("/{order_id}/cancel", summary="Anular orden de venta")
def cancel_order(
    order_id: str,
    request: Request,
    payload: sch.CancelOrderIn | None = Body(None),
    actor: AuthUser = Depends(_edit),
):
    reason = payload.reason if payload else None
    return svc.cancel_sale_order(order_id, actor, request_meta(request), reason=reason)