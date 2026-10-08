from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request
from pydantic import Field

from app.schemas.base import ApiModel
from app.security.deps import AuthUser, request_meta, require_permission
from app.services import invoices as svc

router = APIRouter(prefix="/sales-invoices", tags=["sales-invoices"])

_view = require_permission("receipts", "view")
_edit = require_permission("receipts", "edit")


class TaxStatusIn(ApiModel):
    tax_status: str = Field(pattern="^(PENDING|ACCEPTED|REJECTED|VOIDED|Pendiente|Aceptado|Rechazado|Anulado)$")
    void_reason: str | None = None


class VoidIn(ApiModel):
    reason: str = Field(min_length=1, max_length=500)


@router.get("")
def list_(
    q: Annotated[str | None, Query(max_length=100)] = None,
    tax_status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    _: AuthUser = Depends(_view),
):
    return svc.list_invoices(q=q, tax_status=tax_status, limit=limit, offset=offset)


@router.get("/{invoice_id}")
def get_(invoice_id: str, _: AuthUser = Depends(_view)):
    return svc.get_invoice(invoice_id)


@router.post("/{invoice_id}/tax-status")
def tax_status_(
    invoice_id: str,
    request: Request,
    payload: TaxStatusIn = Body(...),
    actor: AuthUser = Depends(_edit),
):
    return svc.set_tax_status(
        invoice_id, payload.tax_status, actor, request_meta(request), void_reason=payload.void_reason
    )


@router.post("/{invoice_id}/void")
def void_(
    invoice_id: str,
    request: Request,
    payload: VoidIn = Body(...),
    actor: AuthUser = Depends(_edit),
):
    return svc.void_with_credit_note(invoice_id, payload.reason, actor, request_meta(request))