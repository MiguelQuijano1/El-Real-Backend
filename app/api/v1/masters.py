from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, Request, Response

from app.core.errors import bad_request
from app.schemas import masters as m
from app.security.deps import AuthUser, request_meta, require_permission
from app.services import masters as svc
from app.services.masters import MasterConfig

# NOTA: este archivo no usa `from __future__ import annotations` a propósito: FastAPI necesita
# resolver las anotaciones de los modelos creados dentro de la fábrica.


def build_master_router(cfg: MasterConfig, create_model: type, update_model: type, filter_params: tuple[str, ...] = ()) -> APIRouter:
    router = APIRouter()
    view = require_permission(cfg.module, "view")
    create = require_permission(cfg.module, "create")
    edit = require_permission(cfg.module, "edit")
    delete = require_permission(cfg.module, "delete")

    @router.get("", summary=f"Listar {cfg.module}")
    def list_(
        request: Request,
        q: Annotated[str | None, Query(max_length=100)] = None,
        status: Annotated[str | None, Query(pattern="^(ACTIVE|INACTIVE)$")] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        _: AuthUser = Depends(view),
    ):
        extra = {k: v for k, v in request.query_params.items() if k in cfg.filters}
        return svc.list_records(cfg, q=q, status=status, extra=extra, limit=limit, offset=offset)

    @router.get("/{record_id}", summary=f"Obtener {cfg.module}")
    def get_(record_id: str, _: AuthUser = Depends(view)):
        return svc.get_record(cfg, _uuid(record_id))

    @router.post("", status_code=201, summary=f"Crear en {cfg.module}")
    def create_(request: Request, payload: create_model = Body(...), actor: AuthUser = Depends(create)):  # type: ignore[valid-type]
        return svc.create_record(cfg, payload.model_dump(mode="json"), actor, request_meta(request))  # type: ignore[attr-defined]

    @router.patch("/{record_id}", summary=f"Editar en {cfg.module}")
    def update_(record_id: str, request: Request, payload: update_model = Body(...), actor: AuthUser = Depends(edit)):  # type: ignore[valid-type]
        patch = payload.model_dump(mode="json", exclude_unset=True)  # type: ignore[attr-defined]
        return svc.update_record(cfg, _uuid(record_id), patch, actor, request_meta(request))

    @router.delete("/{record_id}", status_code=204, summary=f"Dar de baja en {cfg.module}")
    def delete_(record_id: str, request: Request, actor: AuthUser = Depends(delete)):
        svc.deactivate_record(cfg, _uuid(record_id), actor, request_meta(request))
        return Response(status_code=204)

    return router


def _uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        raise bad_request("Invalid id") from None


MASTERS: list[tuple[str, MasterConfig, type, tuple[str, ...]]] = [
    ("customers", MasterConfig("customers", "customers", "Customer", "Cliente", "legal_name",
                               ("legal_name", "document_number", "code", "contact_name"), "CLI",
                               filters=("salesperson_user_id", "document_type")), m.CustomerIn, ()),
    ("suppliers", MasterConfig("suppliers", "suppliers", "Supplier", "Proveedor", "legal_name",
                               ("legal_name", "tax_id", "code", "contact_name"), "PRV",
                               filters=("business_category",)), m.SupplierIn, ()),
    ("products", MasterConfig("products", "products", "Product", "Producto", "sku",
                              ("name", "sku"), None,
                              filters=("category_id", "primary_supplier_id")), m.ProductIn, ()),
    ("categories", MasterConfig("product_categories", "categories", "ProductCategory", "Categoría", "name",
                                ("name", "code"), "CAT", has_created_at=False,
                                filters=("parent_category_id",)), m.CategoryIn, ()),
    ("warehouses", MasterConfig("warehouses", "warehouses", "Warehouse", "Almacén", "name",
                                ("name", "code", "district"), "ALM", has_created_at=False,
                                filters=("warehouse_type",)), m.WarehouseIn, ()),
    ("drivers", MasterConfig("drivers", "drivers", "Driver", "Conductor", "full_name",
                             ("full_name", "national_id", "license_number", "code"), "CON",
                             filters=("carrier_supplier_id",)), m.DriverIn, ()),
    ("vehicles", MasterConfig("vehicles", "vehicles", "Vehicle", "Vehículo", "plate",
                              ("plate", "brand", "model"), None,
                              filters=("carrier_supplier_id",)), m.VehicleIn, ()),
    # Lista de precios por proveedor. Usa los permisos de Proveedores.
    ("supplier-products", MasterConfig("supplier_products", "suppliers", "SupplierProduct", "Precio de proveedor", "supplier_id",
                                     ("supplier_sku", "notes"), None,
                                     filters=("supplier_id", "product_id")), m.SupplierProductIn, ()),
]


def master_routers() -> list[tuple[str, APIRouter]]:
    out = []
    for prefix, cfg, create_model, _ in MASTERS:
        update_model = m.partial(create_model, f"{create_model.__name__}Update")
        out.append((prefix, build_master_router(cfg, create_model, update_model)))
    return out
