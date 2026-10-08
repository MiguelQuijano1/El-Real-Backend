from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request, Response

from app.schemas import quotations as sch
from app.security.deps import AuthUser, request_meta, require_permission
from app.services import quotations as svc

router = APIRouter(prefix="/quotations", tags=["quotations"])

_view = require_permission("quotations", "view")
_create = require_permission("quotations", "create")
_edit = require_permission("quotations", "edit")
_delete = require_permission("quotations", "delete")


@router.get("")
def list_(
    q: Annotated[str | None, Query(max_length=100)] = None,
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    _: AuthUser = Depends(_view),
):
    return svc.list_quotations(q=q, status=status, limit=limit, offset=offset)


@router.get("/{quotation_id}")
def get_(quotation_id: str, _: AuthUser = Depends(_view)):
    return svc.get_quotation(quotation_id)


@router.post("", status_code=201)
def create_(request: Request, payload: sch.QuotationCreate = Body(...), actor: AuthUser = Depends(_create)):
    return svc.create_quotation(payload.model_dump(mode="json"), actor, request_meta(request))


@router.patch("/{quotation_id}")
def update_(
    quotation_id: str,
    request: Request,
    payload: sch.QuotationUpdate = Body(...),
    actor: AuthUser = Depends(_edit),
):
    return svc.update_quotation(
        quotation_id, payload.model_dump(mode="json", exclude_unset=True), actor, request_meta(request)
    )


@router.post("/{quotation_id}/status")
def status_(
    quotation_id: str,
    request: Request,
    payload: sch.QuotationStatusIn = Body(...),
    actor: AuthUser = Depends(_edit),
):
    return svc.set_status(
        quotation_id, payload.status, actor, request_meta(request), rejected_reason=payload.rejected_reason
    )


@router.post("/{quotation_id}/convert")
def convert_(quotation_id: str, request: Request, actor: AuthUser = Depends(_create)):
    # Convertir requiere crear orden de venta
    return svc.convert_quotation(quotation_id, actor, request_meta(request))


@router.delete("/{quotation_id}", status_code=204)
def delete_(quotation_id: str, request: Request, actor: AuthUser = Depends(_delete)):
    svc.delete_quotation(quotation_id, actor, request_meta(request))
    return Response(status_code=204)