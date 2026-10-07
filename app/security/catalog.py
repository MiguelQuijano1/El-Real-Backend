"""Catálogo de módulos y acciones. Espeja la matriz de permisos del frontend (src/data/permissions.ts):
las claves son estables (en inglés) y `label` es el nombre que muestra la UI."""

PERMISSION_ACTIONS = ["view", "create", "edit", "delete", "approve"]

PERMISSION_GROUPS = [
    {"group": "Inicio", "modules": [{"key": "dashboard", "label": "Dashboard"}]},
    {"group": "Ventas", "modules": [
        {"key": "quotations", "label": "Cotizaciones"},
        {"key": "sales-orders", "label": "Órdenes de venta"},
        {"key": "receipts", "label": "Comprobantes"},
    ]},
    {"group": "Compras", "modules": [
        {"key": "purchase-requests", "label": "Solicitudes"},
        {"key": "purchase-orders", "label": "Órdenes de compra"},
        {"key": "goods-receipts", "label": "Recepción"},
    ]},
    {"group": "Inventario", "modules": [
        {"key": "stock", "label": "Stock por almacén"},
        {"key": "kardex", "label": "Kardex"},
        {"key": "transfers", "label": "Transferencias"},
        {"key": "adjustments", "label": "Ajustes"},
    ]},
    {"group": "Finanzas", "modules": [
        {"key": "receivables", "label": "Cuentas por cobrar"},
        {"key": "payables", "label": "Cuentas por pagar"},
        {"key": "cash", "label": "Caja"},
        {"key": "banks", "label": "Bancos"},
    ]},
    {"group": "Documentos y reportes", "modules": [
        {"key": "documents", "label": "Documentos"},
        {"key": "reports", "label": "Reportes"},
    ]},
    {"group": "Mantenimiento", "modules": [
        {"key": "customers", "label": "Clientes"},
        {"key": "suppliers", "label": "Proveedores"},
        {"key": "products", "label": "Productos"},
        {"key": "categories", "label": "Categorías"},
        {"key": "warehouses", "label": "Almacenes"},
        {"key": "drivers", "label": "Conductores"},
        {"key": "vehicles", "label": "Vehículos"},
    ]},
    {"group": "Sistema", "modules": [
        {"key": "users", "label": "Usuarios"},
        {"key": "roles", "label": "Roles"},
        {"key": "permissions", "label": "Permisos"},
        {"key": "audit", "label": "Auditoría"},
        {"key": "settings", "label": "Configuración"},
    ]},
]

ALL_MODULE_KEYS: list[str] = [m["key"] for g in PERMISSION_GROUPS for m in g["modules"]]

# Código del rol con acceso total (se salta la matriz).
ADMIN_ROLE_CODE = "ADMIN"
