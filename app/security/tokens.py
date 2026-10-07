import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.core.config import get_settings

ALGORITHM = "HS256"


def create_access_token(user_id: str, session_id: str) -> tuple[str, datetime]:
    s = get_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=s.jwt_expires_seconds)
    token = jwt.encode(
        {"sub": user_id, "sid": session_id, "iat": now, "exp": expires_at},
        s.jwt_secret,
        algorithm=ALGORITHM,
    )
    return token, expires_at


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=[ALGORITHM], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        return None
    if not payload.get("sub") or not payload.get("sid"):
        return None
    return payload


def hash_token(token: str) -> str:
    """Las sesiones se guardan hasheadas: un volcado de la BD no permite reutilizar tokens."""
    return hashlib.sha256(token.encode()).hexdigest()
