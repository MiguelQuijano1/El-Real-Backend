from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile

from app.core.serialization import camel
from app.security.deps import AuthUser, request_meta, require_permission
from app.services import documents as svc

router = APIRouter(prefix="/documents", tags=["documents"])

Category = Literal["INVOICE", "CONTRACT", "PURCHASE_ORDER", "DISPATCH_GUIDE", "OTHER", "ATTACHMENT"]


@router.get("")
def list_documents(
    category: Category | None = None,
    order_id: UUID | None = None,
    customer_id: UUID | None = None,
    supplier_id: UUID | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    _: AuthUser = Depends(require_permission("documents", "view")),
):
    return camel(svc.list_documents(
        category=category, order_id=str(order_id) if order_id else None,
        customer_id=str(customer_id) if customer_id else None,
        supplier_id=str(supplier_id) if supplier_id else None, q=q, limit=limit, offset=offset,
    ))


@router.post("", status_code=201)
def upload_document(
    request: Request,
    file: UploadFile = File(...),
    category: Category = Form(...),
    version_label: Annotated[str, Form(alias="versionLabel", max_length=40)] = "v1",
    related_order_id: Annotated[UUID | None, Form(alias="relatedOrderId")] = None,
    related_customer_id: Annotated[UUID | None, Form(alias="relatedCustomerId")] = None,
    related_supplier_id: Annotated[UUID | None, Form(alias="relatedSupplierId")] = None,
    actor: AuthUser = Depends(require_permission("documents", "create")),
):
    related = {
        "order_id": str(related_order_id) if related_order_id else None,
        "customer_id": str(related_customer_id) if related_customer_id else None,
        "supplier_id": str(related_supplier_id) if related_supplier_id else None,
    }
    return camel(svc.upload_document(file, category=category, version_label=version_label, related=related,
                                     actor=actor, meta=request_meta(request)))


@router.get("/{document_id}/download-url")
def download_url(document_id: UUID, request: Request, actor: AuthUser = Depends(require_permission("documents", "view"))):
    """URL firmada y de corta duración (60 s); el bucket es privado."""
    return camel(svc.signed_download_url(str(document_id), actor, request_meta(request)))


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: UUID, request: Request, actor: AuthUser = Depends(require_permission("documents", "delete"))):
    svc.delete_document(str(document_id), actor, request_meta(request))
    return Response(status_code=204)
