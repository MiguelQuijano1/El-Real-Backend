from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request, Response

from app.schemas import purchase_requests as sch
from app.security.deps import AuthUser, request_meta, require_permission
from app.services import purchase_requests as svc

router = APIRouter(prefix="/purchase-requests", tags=["purchase-requests"])

_view = require_permission("purchase-requests", "view")
_create = require_permission("purchase-requests", "create")
_edit = require_permission("purchase-requests", "edit")
_delete = require_permission("purchase-requests", "delete")
_approve = require_permission("purchase-requests", "approve")


@router.get("")
def list_(
    q: Annotated[str | None, Query(max_length=100)] = None,
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    _: AuthUser = Depends(_view),
):
    return svc.list_requests(q=q, status=status, limit=limit, offset=offset)


@router.get("/{request_id}")
def get_(request_id: str, _: AuthUser = Depends(_view)):
    return svc.get_request(request_id)


@router.post("", status_code=201)
def create_(request: Request, payload: sch.PurchaseRequestCreate = Body(...), actor: AuthUser = Depends(_create)):
    return svc.create_request(payload.model_dump(mode="json"), actor, request_meta(request))


@router.patch("/{request_id}")
def update_(
    request_id: str,
    request: Request,
    payload: sch.PurchaseRequestUpdate = Body(...),
    actor: AuthUser = Depends(_edit),
):
    return svc.update_request(request_id, payload.model_dump(mode="json", exclude_unset=True), actor, request_meta(request))


@router.post("/{request_id}/status")
def status_(
    request_id: str,
    request: Request,
    payload: sch.PurchaseRequestStatusIn = Body(...),
    actor: AuthUser = Depends(_approve),
):
    return svc.set_status(request_id, payload.status, actor, request_meta(request), rejection_reason=payload.rejection_reason)


@router.post("/{request_id}/convert")
def convert_(
    request_id: str,
    request: Request,
    payload: sch.ConvertRequestIn = Body(...),
    actor: AuthUser = Depends(_create),
):
    return svc.convert_request(request_id, payload.model_dump(mode="json"), actor, request_meta(request))


@router.delete("/{request_id}", status_code=204)
def delete_(request_id: str, request: Request, actor: AuthUser = Depends(_delete)):
    svc.delete_request(request_id, actor, request_meta(request))
    return Response(status_code=204)