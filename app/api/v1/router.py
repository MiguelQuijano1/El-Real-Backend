from fastapi import APIRouter

from app.api.v1 import (
    audit, auth, dashboard, documents, goods_receipts, health, invoices, orders,
    permissions, purchase_orders, purchase_requests, quotations, roles, settings, users,
)
from app.api.v1.masters import master_routers

api_router = APIRouter(prefix="/api/v1")
for module in (
    health, auth, users, roles, permissions, audit, settings, documents, dashboard,
    orders, quotations, invoices, purchase_requests, purchase_orders, goods_receipts,
):
    api_router.include_router(module.router)
for prefix, router in master_routers():
    api_router.include_router(router, prefix=f"/{prefix}", tags=[prefix])