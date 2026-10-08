from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request

from app.schemas import inventory as sch
from app.security.deps import AuthUser, request_meta, require_permission
from app.services import inventory as svc

router = APIRouter(tags=["inventory"])


# ---- stock ----
@router.get("/stock")
def stock_list(
    q: Annotated[str | None, Query(max_length=100)] = None,
    warehouse_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
    _: AuthUser = Depends(require_permission("stock", "view")),
):
    return svc.list_stock(q=q, warehouse_id=warehouse_id, limit=limit, offset=offset)


# ---- kardex ----
@router.get("/kardex")
def kardex_list(
    q: Annotated[str | None, Query(max_length=100)] = None,
    product_id: Annotated[str | None, Query()] = None,
    warehouse_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    _: AuthUser = Depends(require_permission("kardex", "view")),
):
    return svc.list_kardex(q=q, product_id=product_id, warehouse_id=warehouse_id, limit=limit, offset=offset)


# ---- transfers ----
@router.get("/transfers")
def transfers_list(
    q: Annotated[str | None, Query(max_length=100)] = None,
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    _: AuthUser = Depends(require_permission("transfers", "view")),
):
    return svc.list_transfers(q=q, status=status, limit=limit, offset=offset)


@router.get("/transfers/{transfer_id}")
def transfers_get(transfer_id: str, _: AuthUser = Depends(require_permission("transfers", "view"))):
    return svc.get_transfer(transfer_id)


@router.post("/transfers", status_code=201)
def transfers_create(
    request: Request,
    payload: sch.TransferCreate = Body(...),
    actor: AuthUser = Depends(require_permission("transfers", "create")),
):
    return svc.create_transfer(payload.model_dump(mode="json"), actor, request_meta(request))


@router.post("/transfers/{transfer_id}/dispatch")
def transfers_dispatch(
    transfer_id: str,
    request: Request,
    actor: AuthUser = Depends(require_permission("transfers", "edit")),
):
    return svc.dispatch_transfer(transfer_id, actor, request_meta(request))


@router.post("/transfers/{transfer_id}/receive")
def transfers_receive(
    transfer_id: str,
    request: Request,
    actor: AuthUser = Depends(require_permission("transfers", "edit")),
):
    return svc.receive_transfer(transfer_id, actor, request_meta(request))


@router.post("/transfers/{transfer_id}/cancel")
def transfers_cancel(
    transfer_id: str,
    request: Request,
    actor: AuthUser = Depends(require_permission("transfers", "edit")),
):
    return svc.cancel_transfer(transfer_id, actor, request_meta(request))


# ---- adjustments ----
@router.get("/adjustments")
def adjustments_list(
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    _: AuthUser = Depends(require_permission("adjustments", "view")),
):
    return svc.list_adjustments(q=q, limit=limit, offset=offset)


@router.post("/adjustments", status_code=201)
def adjustments_create(
    request: Request,
    payload: sch.AdjustmentCreate = Body(...),
    actor: AuthUser = Depends(require_permission("adjustments", "create")),
):
    return svc.create_adjustment(payload.model_dump(mode="json"), actor, request_meta(request))