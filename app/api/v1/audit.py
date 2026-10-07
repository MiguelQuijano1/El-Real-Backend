from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.serialization import camel
from app.security.deps import AuthUser, require_permission
from app.services import audit_query

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def list_events(
    module: Annotated[str | None, Query(max_length=80)] = None,
    action: Literal["LOGIN", "CREATE", "EDIT", "DELETE", "EXPORT", "DOWNLOAD"] | None = None,
    result: Literal["SUCCESS", "FAILURE", "DENIED"] | None = None,
    user_id: UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    _: AuthUser = Depends(require_permission("audit", "view")),
):
    return camel(audit_query.list_events(
        module=module, action=action, result=result, user_id=str(user_id) if user_id else None,
        date_from=date_from, date_to=date_to, limit=limit, offset=offset,
    ))
