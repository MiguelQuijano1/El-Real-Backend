"""Seed inicial e idempotente: se puede ejecutar varias veces sin duplicar nada.

Crea el catálogo de permisos, los roles base (con la misma matriz por defecto del frontend),
la configuración de la empresa y el primer usuario Administrador.

Uso:  python -m scripts.seed      (requiere SUPABASE_*, SEED_ADMIN_EMAIL y SEED_ADMIN_PASSWORD en .env)
"""
from app.core.config import get_settings
from app.repositories.client import db, execute, first, rows
from app.security.catalog import ADMIN_ROLE_CODE, PERMISSION_ACTIONS, PERMISSION_GROUPS
from app.security.passwords import hash_password

FULL = (1, 1, 1, 1, 1)
VIEW = (1, 0, 0, 0, 0)
WORK = (1, 1, 1, 0, 0)
NONE = (0, 0, 0, 0, 0)


def _in(m: str, names: list[str]) -> bool:
    return m in names


# Misma matriz por defecto que defaultPermissions() del frontend (src/data/permissions.ts).
ROLES = [
    dict(code=ADMIN_ROLE_CODE, name="Administrador", type="SYSTEM", description="Acceso total y configuración del sistema",
         flags=lambda g, m: FULL),
    dict(code="MANAGER", name="Gerente", type="SYSTEM", description="Aprueba compras, descuentos y anulaciones",
         flags=lambda g, m: VIEW if g == "Sistema" else FULL),
    dict(code="SALES", name="Ventas", type="CUSTOM", description="Cotizaciones, órdenes de venta y clientes",
         flags=lambda g, m: (((1, 1, 0, 0, 0) if m == "receipts" else WORK) if g == "Ventas"
                             else WORK if m == "customers"
                             else VIEW if _in(m, ["dashboard", "reports", "stock", "documents", "products"]) else NONE)),
    dict(code="PURCHASING", name="Compras", type="CUSTOM", description="Solicitudes, órdenes de compra y proveedores",
         flags=lambda g, m: (WORK if g == "Compras" or m == "suppliers"
                             else VIEW if _in(m, ["dashboard", "reports", "stock", "kardex", "documents", "products", "payables"]) else NONE)),
    dict(code="WAREHOUSE", name="Almacén", type="CUSTOM", description="Despachos, recepciones, transferencias y ajustes",
         flags=lambda g, m: (WORK if g == "Inventario" or m == "goods-receipts"
                             else (1, 0, 1, 0, 0) if m == "sales-orders"
                             else (1, 1, 0, 0, 0) if m == "purchase-requests"
                             else VIEW if _in(m, ["dashboard", "products", "warehouses", "documents"]) else NONE)),
    dict(code="ACCOUNTING", name="Contabilidad", type="CUSTOM", description="Comprobantes, cuentas por cobrar y pagar, caja y bancos",
         flags=lambda g, m: (FULL if g == "Finanzas"
                             else (1, 1, 1, 0, 1) if m == "receipts"
                             else VIEW if _in(m, ["dashboard", "reports", "documents", "customers", "suppliers", "audit"]) else NONE)),
]


def seed_permissions() -> dict[str, str]:
    wanted = [{"module_key": mod["key"], "action_key": a} for g in PERMISSION_GROUPS for mod in g["modules"] for a in PERMISSION_ACTIONS]
    execute(db().table("permissions").upsert(wanted, on_conflict="module_key,action_key", ignore_duplicates=True))
    return {f"{p['module_key']}:{p['action_key']}": p["id"] for p in rows(db().table("permissions").select("id, module_key, action_key"))}


def seed_roles(permission_ids: dict[str, str]) -> dict[str, str]:
    ids: dict[str, str] = {}
    for role in ROLES:
        execute(db().table("roles").upsert(
            [{"code": role["code"], "name": role["name"], "description": role["description"], "role_type": role["type"]}],
            on_conflict="code", ignore_duplicates=True))
        row = first(db().table("roles").select("id").eq("code", role["code"]))
        ids[role["code"]] = row["id"]

        # Solo se crean los permisos si el rol no tiene ninguno: nunca se pisan los cambios hechos desde Permisos.
        existing = execute(db().table("role_permissions").select("role_id", count="exact").eq("role_id", row["id"]).limit(1)).count
        if existing:
            continue
        data = [
            {"role_id": row["id"], "permission_id": permission_ids[f"{mod['key']}:{action}"], "granted": bool(flags[i])}
            for g in PERMISSION_GROUPS for mod in g["modules"]
            for flags in [role["flags"](g["group"], mod["key"])]
            for i, action in enumerate(PERMISSION_ACTIONS)
        ]
        for start in range(0, len(data), 500):
            execute(db().table("role_permissions").insert(data[start:start + 500]))
    return ids


def seed_company_settings() -> None:
    """Datos de ejemplo del frontend (DEFAULT_SETTINGS); edítalos desde Configuración."""
    execute(db().table("company_settings").upsert([{
        "id": 1, "tax_id": "20601987451", "legal_name": "Comercializadora Andina de Materiales S.A.C.",
        "fiscal_address": "Av. Argentina 1820, Cercado de Lima, Lima",
        "invoice_series": "F001", "receipt_series": "B001", "dispatch_series": "T001", "credit_note_series": "FC01",
    }], on_conflict="id", ignore_duplicates=True))


def seed_admin(role_id: str) -> None:
    s = get_settings()
    email = (s.seed_admin_email or "").strip().lower()
    password = s.seed_admin_password
    if not email or not password:
        print("SEED_ADMIN_EMAIL / SEED_ADMIN_PASSWORD no definidos: no se crea el usuario administrador.")
        return
    if len(password) < 12:
        raise SystemExit("SEED_ADMIN_PASSWORD debe tener al menos 12 caracteres.")
    if first(db().table("users").select("id").eq("email", email)):
        print(f"El usuario {email} ya existe; no se modifica.")
        return
    execute(db().table("users").insert({
        "full_name": s.seed_admin_name.strip(), "email": email, "password_hash": hash_password(password),
        "area": "Sistemas", "status": "ACTIVE", "role_id": role_id,
    }))
    print(f"Administrador creado: {email}")


def main() -> None:
    permission_ids = seed_permissions()
    role_ids = seed_roles(permission_ids)
    seed_company_settings()
    seed_admin(role_ids[ADMIN_ROLE_CODE])
    print("Seed completado.")


if __name__ == "__main__":
    main()
