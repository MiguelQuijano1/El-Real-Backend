from fastapi import APIRouter

from app.api.v1 import audit, auth, documents, health, permissions, roles, settings, users
from app.api.v1.masters import master_routers

api_router = APIRouter(prefix="/api/v1")
for module in (health, auth, users, roles, permissions, audit, settings, documents):
    api_router.include_router(module.router)
for prefix, router in master_routers():
    api_router.include_router(router, prefix=f"/{prefix}", tags=[prefix])
