# ERP EnterpriseCloud · Backend (FastAPI + Supabase)

API REST en **Python + FastAPI** con **PostgreSQL en Supabase** (librería oficial `supabase-py`, sin ORM).
Reemplaza al backend NestJS + Prisma de la rama `feature-authentication` y sigue el Documento Maestro
(API REST versionada en `/api/v1`, PostgreSQL, almacenamiento de objetos, seguridad por roles y auditoría).

**Estado:** Fase 6 parcial. Incluye seguridad completa (autenticación, usuarios, roles, permisos, auditoría),
configuración de empresa, **maestros** (clientes, proveedores, productos, categorías, almacenes, conductores,
vehículos) y **documentos** con almacenamiento privado. Los flujos transaccionales (cotizaciones, órdenes,
inventario/kardex, comprobantes, caja y bancos, reportes) siguen pendientes (ver el final).

## Puesta en marcha

Requisitos: Python 3.11+ y un proyecto de Supabase.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
```

**1. Crear el esquema en Supabase** — en *SQL Editor* ejecuta, en este orden:

| Archivo | Qué hace |
|---|---|
| `database/001_schema.sql` | 34 tablas, enums, índices, triggers de `updated_at` y RLS (idéntico al `schema.prisma` anterior) |
| `database/002_functions.sql` | `register_failed_login` y `next_sequence` (atómicas) y sus permisos |
| `database/003_storage.sql` | Bucket **privado** `documents` |

**2. Configurar `.env`** (ya incluido, solo faltan tus credenciales):

- `SUPABASE_URL` y `SUPABASE_SERVICE_ROLE_KEY` → *Project Settings → API*. Sirve la `service_role` clásica o la nueva *secret key*.
- `SEED_ADMIN_PASSWORD` → mínimo 12 caracteres (es la clave del primer administrador).
- `JWT_SECRET` ya viene generado para desarrollo; para producción genera otro.

**3. Datos iniciales y arranque**

```bash
python -m scripts.seed               # permisos, roles base, configuración y administrador (idempotente)
uvicorn app.main:app --reload        # http://localhost:8000  ·  documentación en /docs
```

Prueba rápida:

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "content-type: application/json" \
  -d '{"email":"admin@tuempresa.pe","password":"<tu SEED_ADMIN_PASSWORD>"}'
```

## Seguridad

- **Todo requiere sesión** (JWT `Bearer`) salvo `POST /auth/login` y `GET /health`.
- **Denegar por defecto:** cada ruta declara `public`, `authenticated` o `require_permission(módulo, acción)`
  (`view`, `create`, `edit`, `delete`, `approve`). FastAPI permite todo por defecto, así que
  `tests/test_route_policies.py` **falla si alguna ruta no declara política**. El rol `ADMIN` tiene acceso total.
- **Bloqueo de cuenta** al llegar a `maxLoginAttempts` (Configuración, 5 por defecto), con contador atómico en SQL.
  Se desbloquea con `PATCH /users/{id}/status`.
- **Límite de peticiones:** 10 logins/min y 5 cambios de contraseña/min por IP; 120 peticiones/min en general.
  Es en memoria (una instancia). Con varias instancias usa además un WAF/API Gateway.
  Detrás de un proxy (Nginx, etc.) pon `TRUST_PROXY=true` para usar la IP real.
- Un usuario con `users:create/edit` **no puede** crear, editar ni asignar el rol Administrador, y nadie cambia su propio estado o rol.
- Contraseñas con **argon2id**; las sesiones se guardan **hasheadas** y se revocan con logout, cambio o restablecimiento de contraseña.
- Todas las tablas tienen **RLS activo sin políticas**: la `anon key` pública de Supabase no puede leer ni escribir nada. Solo este backend accede (service_role).
  **Nunca pongas la `service_role` key en el frontend ni en Git.**
- Documentos: bucket privado, tipos permitidos (PDF, imágenes, Office, CSV, TXT, ZIP), límite `MAX_UPLOAD_MB`, URLs de descarga firmadas de 60 s, borrado lógico.
- Todo ingreso, cambio y descarga queda en `audit_events`.

## Endpoints (`/api/v1`)

| Ruta | Permiso |
|---|---|
| `POST /auth/login` · `POST /auth/logout` · `GET /auth/me` · `POST /auth/change-password` | público / sesión |
| `GET/POST /users` · `GET /users/{id}` · `GET /users/by-email/{email}` · `PATCH /users/{id}` · `PATCH /users/{id}/status` · `PATCH /users/{id}/role` · `POST /users/{id}/reset-password` | `users` |
| `GET/POST /roles` · `GET/PATCH /roles/{id}` | `roles` |
| `GET/PUT /roles/{id}/permissions` (PUT reemplaza la matriz) · `GET /permissions` · `GET /permissions/catalog` | `permissions` |
| `GET /audit` (filtros: `module`, `action`, `result`, `user_id`, `date_from`, `date_to`, `limit`, `offset`) | `audit:view` |
| `GET/PATCH /settings` | `settings` |
| `GET/POST /customers` · `/suppliers` · `/products` · `/categories` · `/warehouses` · `/drivers` · `/vehicles` y `GET/PATCH/DELETE /{id}` | un módulo por recurso |
| `GET/POST /documents` · `GET /documents/{id}/download-url` · `DELETE /documents/{id}` | `documents` |
| `GET /health` | público |

Listados: `?q=` (búsqueda), `?status=ACTIVE|INACTIVE`, `?limit=` (máx. 200), `?offset=` → `{ items, total, limit, offset }`.
`DELETE` en maestros es **baja lógica** (`status = INACTIVE`). Los códigos (`CLI-00001`, `PRV-…`, `CAT-…`, `ALM-…`, `CON-…`) se generan si no se envían.
`averageCost` de un producto no se acepta: lo calculará el sistema con el kardex.

Respuestas en **camelCase**; errores: `{ statusCode, message, timestamp, path, code? }` (`code` solo en `ACCOUNT_LOCKED` y `ACCOUNT_INACTIVE`).

### Cambios respecto al backend NestJS
- Prefijo `/api` → **`/api/v1`**.
- Se mantienen los cuerpos y respuestas de login, `me`, usuarios y roles (`accessToken`, `permissions`, `fullName`, `roleId`…).
- Nuevos: `/audit`, `/settings`, maestros, documentos, `POST /roles`, `PATCH /roles/{id}` y `PUT /roles/{id}/permissions`.
- Los errores de validación devuelven 400 con la lista de mensajes (igual que Nest).

## Pruebas

```bash
pytest                                # 9 pruebas sin base de datos (seguridad, políticas de rutas, utilidades)
TEST_POSTGREST_URL=http://localhost:3001 pytest   # + 21 de integración contra un PostgREST real
```

Las de integración necesitan una base con `001` y `002` aplicados y un PostgREST apuntando a ella
(el mismo motor que usa Supabase). Sin esa variable se omiten. El Storage se prueba con un simulado en memoria:
**la subida/descarga real contra tu bucket de Supabase conviene probarla una vez a mano** (`/docs` → `POST /documents`).

## Estructura (según el Documento Maestro)

```
app/
├── api/v1/          rutas (auth, users, roles, permissions, audit, settings, documents, masters, health)
├── core/            configuración, errores, límite de peticiones, serialización
├── schemas/         modelos Pydantic de entrada/validación
├── security/        argon2, JWT, catálogo de permisos, dependencias de acceso
├── services/        lógica de negocio
├── repositories/    cliente de Supabase (único punto de acceso a la BD)
└── main.py
database/            SQL para Supabase (esquema, funciones, bucket)
scripts/seed.py      datos iniciales
tests/
```

## Despliegue

Por ahora se omite AWS y no se usa Docker. Para producción, ejecuta la API directamente con uvicorn
(detrás de Nginx u otro proxy con HTTPS):

```bash
ENVIRONMENT=production TRUST_PROXY=true uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
```

En producción `/docs` y `/openapi.json` se desactivan. Limita `CORS_ORIGINS` al dominio del frontend.

## Lo que falta (siguientes fases)

PostgREST no tiene transacciones entre varias llamadas. Los flujos que mueven stock o dinero
(orden de venta → despacho → kardex, recepción → inventario, cobros y pagos, transferencias) deben
implementarse como **funciones SQL (`rpc`)** para que sean atómicos. Las tablas, enums y la tabla de
secuencias ya existen en `001_schema.sql`; el patrón a seguir es `register_failed_login` / `next_sequence`.

Pendiente: cotizaciones, solicitudes y órdenes de compra/venta, despachos y recepciones, comprobantes,
inventario (stock, kardex, transferencias, ajustes), caja y bancos, dashboard y reportes.
El frontend actual sigue usando datos locales (`localStorage`); falta conectarlo a esta API.
