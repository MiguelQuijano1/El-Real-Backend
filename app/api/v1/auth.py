from fastapi import APIRouter, Depends, Request, Response

from app.core.rate_limit import rate_limit
from app.core.serialization import camel
from app.schemas.auth import ChangePasswordIn, LoginIn
from app.security.deps import AuthUser, authenticated, public, request_meta
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", dependencies=[Depends(public), Depends(rate_limit("login", 10, 60))])
def login(dto: LoginIn, request: Request):
    """Máx. 10 intentos por minuto y por IP, además del bloqueo por cuenta."""
    return camel(auth_service.login(dto, request_meta(request)))


@router.post("/logout", status_code=204)
def logout(request: Request, user: AuthUser = Depends(authenticated)):
    auth_service.logout(user, request_meta(request))
    return Response(status_code=204)


@router.get("/me")
def me(user: AuthUser = Depends(authenticated)):
    return camel(auth_service.me(user))


@router.post("/change-password", status_code=204, dependencies=[Depends(rate_limit("change-password", 5, 60))])
def change_password(dto: ChangePasswordIn, request: Request, user: AuthUser = Depends(authenticated)):
    auth_service.change_password(user, dto, request_meta(request))
    return Response(status_code=204)
