from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response

from app.repositories.client import db
from app.security.deps import public

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", dependencies=[Depends(public)])
def health(response: Response):
    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        db().table("roles").select("id").limit(1).execute()
        return {"status": "ok", "database": "connected", "timestamp": timestamp}
    except Exception:  # noqa: BLE001
        response.status_code = 503
        return {"status": "error", "database": "disconnected", "timestamp": timestamp}
