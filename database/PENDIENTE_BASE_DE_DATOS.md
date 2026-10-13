# Pendiente en la base de datos (Supabase)

Cambios del backend que **necesitan que se ejecute SQL** en la base de datos para funcionar.
Sin esto, crear cotizaciones falla con `400 · Invalid value in request`.

Rama del backend: `feature/compras-kardex-caja`.

## Antes de empezar

- Trabajar en el proyecto de Supabase que usa el backend (el de `SUPABASE_URL` en `.env`).
- Ejecutar en **SQL Editor**. Orden: primero `007`, luego `008`.
- Ambos scripts son **idempotentes**: se pueden correr más de una vez sin dañar datos.
- Hacer respaldo (o probar primero en una rama de Supabase) si la base tiene datos reales.

---

## 1. `007_compras_sin_aprobacion.sql` (obligatorio)

**Para qué:** las órdenes de compra ya no tienen el paso «Aprobar orden». Nacen listas para recepción en Kardex (paso 2).
Este script mueve al paso 2 las órdenes de compra que quedaron en el paso 1 («Por aprobar») con el esquema anterior.

**Qué hace:**
- Registra el evento del paso 1 de esas órdenes (nota: «Migración: orden sin aprobación previa»).
- Pasa `current_step` de 1 a 2 en órdenes de compra no anuladas.

**Qué NO toca:** órdenes anuladas ni órdenes que ya avanzaron de paso. No borra datos.

Archivo: `database/007_compras_sin_aprobacion.sql`

## 2. `008_cotizaciones_sin_cliente.sql` (obligatorio, es lo que hoy bloquea)

**Para qué:** una cotización se puede hacer **sin cliente** (el comprador aún no se conoce ni está registrado).
El cliente se elige recién al convertir la cotización en orden de venta.

**Qué hace:**

```sql
alter table quotations alter column customer_id drop not null;
```

**Qué NO hace:** no borra ni modifica filas. La clave foránea hacia `customers` se mantiene (si hay cliente, debe existir).

Archivo: `database/008_cotizaciones_sin_cliente.sql`

---

## Verificación (ejecutar después)

```sql
-- 008: customer_id debe ser nullable → is_nullable = 'YES'
select column_name, is_nullable
from information_schema.columns
where table_schema = 'public' and table_name = 'quotations' and column_name = 'customer_id';

-- 007: no deben quedar órdenes de compra vigentes en el paso 1 → 0
select count(*) as compras_en_paso_1
from business_orders
where kind = 'PURCHASE' and current_step = 1 and not is_cancelled;
```

Prueba funcional: en el sistema, **Ventas › Cotizaciones › Nueva cotización**, agregar un producto y crear.
Debe aparecer como `COT-AAAA-NNNN` con cliente «Sin cliente».

---

## Permisos de roles (no es SQL, se hace desde la pantalla Permisos)

La aprobación de recepción en Kardex es **una sola operación atómica** y valida dos permisos.
Los roles que no sean Administrador necesitan:

| Módulo (pantalla) | Acción | Para qué |
| --- | --- | --- |
| Kardex (`goods-receipts`) | `approve` | Aprobar la recepción de mercadería |
| Cuentas por pagar (`payables`) | `edit` | Registrar el pago/egreso de una compra al contado |

Sin esos permisos, el botón «Aprobar recepción» devuelve 403 para ese rol.
El rol Administrador no necesita cambios.

---

## Comportamientos que conviene conocer

- **Cotización vencida:** no se guarda un estado nuevo. Una cotización en Borrador/Enviada/Aprobada cuya fecha
  «Válida hasta» ya pasó se muestra como **Vencida** (se calcula al leer). Al extender la fecha, vuelve a estar vigente.
  No requiere ningún cambio en la base ni tareas programadas.
- **Venta al contado sin pagar:** la fecha límite de pago se guarda en `order_step_events.form_data` (clave `lim`) del paso de creación.
  Una orden de venta sin aprobar cuya fecha límite pasó se anula sola (al listar o al intentar aprobarla), con auditoría «Sistema».
  Requiere que la columna `form_data` de `order_step_events` exista (ya la usa el resto del flujo); no hay SQL nuevo.
  Las ventas a crédito no se anulan: si no pagan quedan «Vencido» en Cuentas por cobrar.
- **Índice existente:** `quotations_idx_2 (status, valid_until)` ya cubre las consultas por vigencia.
- **Recepción parcial:** la cantidad pendiente no se vuelve a recibir en la misma orden (queda «con observaciones»).
- **Comprobante del proveedor:** el tipo (Factura, Boleta, etc.) se guarda en el evento del paso de la orden,
  no en una columna propia.

## Registro

| Fecha | Quién | Script | Resultado |
| --- | --- | --- | --- |
| | | `007_compras_sin_aprobacion.sql` | |
| | | `008_cotizaciones_sin_cliente.sql` | |
