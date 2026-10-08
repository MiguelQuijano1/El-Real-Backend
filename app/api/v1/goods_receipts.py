from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.security.deps import AuthUser, require_permission
from app.services import receipts as svc

router = APIRouter(prefix="/goods-receipts", tags=["goods-receipts"])

_view = require_permission("goods-receipts", "view")


@router.get("")
def list_(
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    _: AuthUser = Depends(_view),
):
    return svc.list_receipts(q=q, limit=limit, offset=offset)


@router.get("/{receipt_id}")
def get_(receipt_id: str, _: AuthUser = Depends(_view)):
    return svc.get_receipt(receipt_id)