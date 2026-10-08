from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request

from app.schemas import purchase_orders as sch
from app.schemas.orders import CancelOrderIn, CompleteStepIn
from app.security.deps import AuthUser, request_meta, require_permission
from app.services import purchase_orders as svc

router = APIRouter(prefix="/purchase-orders", tags=["purchase-orders"])

_view = require_permission("purchase-orders", "view")
_create = require_permission("purchase-orders", "create")
_edit = require_permission("purchase-orders", "edit")


@router.get("")
def list_(
    q: Annotated[str | None, Query(max_length=100)] = None,
    cancelled: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    _: AuthUser = Depends(_view),
):
    return svc.list_purchase_orders(q=q, cancelled=cancelled, limit=limit, offset=offset)


@router.get("/{order_id}")
def get_(order_id: str, _: AuthUser = Depends(_view)):
    return svc.get_purchase_order(order_id)


@router.post("", status_code=201)
def create_(request: Request, payload: sch.PurchaseOrderCreate = Body(...), actor: AuthUser = Depends(_create)):
    return svc.create_purchase_order(payload.model_dump(mode="json"), actor, request_meta(request))


@router.post("/{order_id}/complete-step")
def complete_step(
    order_id: str,
    request: Request,
    payload: CompleteStepIn = Body(...),
    actor: AuthUser = Depends(_edit),
):
    return svc.complete_step(order_id, payload.model_dump(mode="json"), actor, request_meta(request))


@router.post("/{order_id}/cancel")
def cancel_order(
    order_id: str,
    request: Request,
    payload: CancelOrderIn | None = Body(None),
    actor: AuthUser = Depends(_edit),
):
    reason = payload.reason if payload else None
    return svc.cancel_purchase_order(order_id, actor, request_meta(request), reason=reason)