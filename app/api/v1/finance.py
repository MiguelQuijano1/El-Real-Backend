from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request

from app.schemas import finance as sch
from app.security.deps import AuthUser, request_meta, require_permission
from app.services import finance as svc

router = APIRouter(tags=["finance"])


# ---- CxC ----
@router.get("/receivables")
def receivables_list(
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    _: AuthUser = Depends(require_permission("ar", "view")),
):
    return svc.list_receivables(q=q, limit=limit, offset=offset)


@router.post("/receivables/{invoice_id}/collect")
def receivables_collect(
    invoice_id: str,
    request: Request,
    payload: sch.CollectIn = Body(...),
    actor: AuthUser = Depends(require_permission("ar", "edit")),
):
    return svc.collect_receivable(invoice_id, payload.model_dump(mode="json"), actor, request_meta(request))


# ---- CxP ----
@router.get("/payables")
def payables_list(
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    _: AuthUser = Depends(require_permission("ap", "view")),
):
    return svc.list_payables(q=q, limit=limit, offset=offset)


@router.post("/payables/{invoice_id}/pay")
def payables_pay(
    invoice_id: str,
    request: Request,
    payload: sch.PayIn = Body(...),
    actor: AuthUser = Depends(require_permission("ap", "edit")),
):
    return svc.pay_payable(invoice_id, payload.model_dump(mode="json"), actor, request_meta(request))


# ---- Caja ----
@router.get("/cash")
def cash_list(
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    _: AuthUser = Depends(require_permission("cash", "view")),
):
    data = svc.list_cash(q=q, limit=limit, offset=offset)
    data["balance"] = svc.cash_balance()
    return data


@router.post("/cash", status_code=201)
def cash_create(
    request: Request,
    payload: sch.CashMovementIn = Body(...),
    actor: AuthUser = Depends(require_permission("cash", "create")),
):
    return svc.create_cash_movement(payload.model_dump(mode="json"), actor, request_meta(request))


# ---- Bancos ----
@router.get("/bank-accounts")
def bank_accounts(_: AuthUser = Depends(require_permission("banks", "view"))):
    return {"items": svc.list_bank_accounts()}


@router.get("/bank-movements")
def bank_movements_list(
    q: Annotated[str | None, Query(max_length=100)] = None,
    bank_account_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    _: AuthUser = Depends(require_permission("banks", "view")),
):
    return svc.list_bank_movements(q=q, bank_account_id=bank_account_id, limit=limit, offset=offset)


@router.post("/bank-movements", status_code=201)
def bank_movements_create(
    request: Request,
    payload: sch.BankMovementIn = Body(...),
    actor: AuthUser = Depends(require_permission("banks", "create")),
):
    return svc.create_bank_movement(payload.model_dump(mode="json"), actor, request_meta(request))


@router.post("/treasury-movements/{movement_id}/reconcile")
def reconcile(
    movement_id: str,
    request: Request,
    payload: sch.ReconcileIn | None = Body(None),
    actor: AuthUser = Depends(require_permission("banks", "edit")),
):
    ref = payload.reference if payload else None
    return svc.reconcile_movement(movement_id, actor, request_meta(request), reference=ref)