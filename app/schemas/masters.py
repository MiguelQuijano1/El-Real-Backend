from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from pydantic import Field, create_model, model_validator

from app.schemas.base import ApiModel

RecordStatus = Literal["ACTIVE", "INACTIVE"]


def partial(model: type[ApiModel], name: str) -> type[ApiModel]:
    """Versión del modelo con todos los campos opcionales (para PATCH), conservando longitudes y rangos."""
    fields = {n: (Optional[f.annotation], None) for n, f in model.model_fields.items()}
    out = create_model(name, __base__=ApiModel, **fields)  # type: ignore[call-overload]
    for n, f in model.model_fields.items():
        out.model_fields[n].metadata = f.metadata
    out.model_rebuild(force=True)
    return out


class CustomerIn(ApiModel):
    code: str | None = Field(default=None, max_length=20)
    legal_name: str = Field(min_length=1, max_length=200)
    document_type: Literal["RUC", "DNI", "CE"]
    document_number: str = Field(min_length=1, max_length=24)
    contact_name: str | None = Field(default=None, max_length=160)
    phone: str | None = Field(default=None, max_length=40)
    billing_email: str | None = Field(default=None, max_length=254)
    fiscal_address: str | None = None
    payment_terms_days: int = Field(default=0, ge=0, le=365)
    credit_limit: Decimal = Field(default=Decimal("0"), ge=0)
    salesperson_user_id: UUID | None = None
    status: RecordStatus = "ACTIVE"

    @model_validator(mode="after")
    def _document(self):
        n = self.document_number
        if self.document_type == "RUC" and not (n.isdigit() and len(n) == 11):
            raise ValueError("El RUC debe tener 11 dígitos")
        if self.document_type == "DNI" and not (n.isdigit() and len(n) == 8):
            raise ValueError("El DNI debe tener 8 dígitos")
        return self


class SupplierIn(ApiModel):
    code: str | None = Field(default=None, max_length=20)
    legal_name: str = Field(min_length=1, max_length=200)
    tax_id: str = Field(min_length=1, max_length=24)
    contact_name: str | None = Field(default=None, max_length=160)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=254)
    address: str | None = None
    business_category: str | None = Field(default=None, max_length=80)
    payment_terms_days: int = Field(default=0, ge=0, le=365)
    bank_details_label: str | None = None
    status: RecordStatus = "ACTIVE"


class CategoryIn(ApiModel):
    code: str | None = Field(default=None, max_length=20)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    parent_category_id: UUID | None = None
    target_margin_pct: Decimal | None = Field(default=None, ge=0, le=100)
    status: RecordStatus = "ACTIVE"


class ProductIn(ApiModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    category_id: UUID | None = None
    unit: str = Field(min_length=1, max_length=24)
    sale_price: Decimal = Field(ge=0)
    minimum_stock: Decimal = Field(default=Decimal("0"), ge=0)
    primary_supplier_id: UUID | None = None
    taxable: bool = True
    status: RecordStatus = "ACTIVE"
    # average_cost NO se acepta: lo calcula el sistema con los movimientos de inventario.


class WarehouseIn(ApiModel):
    code: str | None = Field(default=None, max_length=20)
    name: str = Field(min_length=1, max_length=120)
    warehouse_type: Literal["MAIN", "SECONDARY", "TRANSIT"]
    address: str | None = None
    district: str | None = Field(default=None, max_length=100)
    manager_user_id: UUID | None = None
    status: RecordStatus = "ACTIVE"


class DriverIn(ApiModel):
    code: str | None = Field(default=None, max_length=20)
    full_name: str = Field(min_length=1, max_length=160)
    national_id: str = Field(min_length=1, max_length=24)
    phone: str | None = Field(default=None, max_length=40)
    license_number: str = Field(min_length=1, max_length=40)
    license_category: str = Field(min_length=1, max_length=16)
    is_company_driver: bool = True
    carrier_supplier_id: UUID | None = None
    status: RecordStatus = "ACTIVE"


class SupplierProductIn(ApiModel):
    """Precio de compra de un producto en un proveedor (sin IGV)."""
    supplier_id: UUID
    product_id: UUID
    supplier_sku: str | None = Field(default=None, max_length=64)
    unit_cost: Decimal = Field(ge=0)
    notes: str | None = None
    status: RecordStatus = "ACTIVE"


class VehicleIn(ApiModel):
    plate: str = Field(min_length=1, max_length=16)
    vehicle_type: str = Field(min_length=1, max_length=32)
    brand: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=80)
    capacity: Decimal | None = Field(default=None, ge=0)
    capacity_unit: str | None = Field(default=None, max_length=12)
    is_company_vehicle: bool = True
    carrier_supplier_id: UUID | None = None
    default_driver_id: UUID | None = None
    status: RecordStatus = "ACTIVE"
