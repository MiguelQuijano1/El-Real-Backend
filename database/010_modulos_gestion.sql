-- Módulos de Gestión (Reposición, Flujo de caja, Metas, Comisiones, Inventario físico, Hoja de ruta).
-- Antes guardaban sus datos en el navegador (localStorage); ahora viven en la base de datos.
-- Idempotente: se puede ejecutar más de una vez sin perder datos.
begin;

-- ───────── Interruptores de módulos opcionales (Configuración › Módulos opcionales) ─────────
-- Una fila por módulo apagado/encendido. Sin fila = encendido.
create table if not exists module_switches (
  "module_id" varchar(40) not null primary key,
  "enabled" boolean not null default true,
  "updated_by_user_id" uuid references users("id") on delete set null,
  "updated_at" timestamptz not null default now()
);

-- ───────── Metas de venta ─────────
create table if not exists sales_goals (
  "id" uuid not null default gen_random_uuid() primary key,
  "period" char(7) not null check ("period" ~ '^\d{4}-(0[1-9]|1[0-2])$'),
  "kind" varchar(12) not null check ("kind" in ('vendedor', 'categoria')),
  "target" varchar(160) not null,
  "amount" numeric(14, 2) not null check ("amount" > 0),
  "created_by_user_id" uuid references users("id") on delete set null,
  "created_at" timestamptz not null default now(),
  "updated_at" timestamptz not null default now(),
  unique ("period", "kind", "target")
);
create index if not exists sales_goals_idx_1 on sales_goals ("period");

-- ───────── Comisiones: % general, % por vendedor y liquidaciones pagadas ─────────
create table if not exists commission_settings (
  "id" smallint not null default 1 primary key check ("id" = 1),
  "default_pct" numeric(5, 2) not null default 2 check ("default_pct" between 0 and 100),
  "updated_at" timestamptz not null default now()
);
insert into commission_settings ("id") values (1) on conflict ("id") do nothing;

create table if not exists commission_rates (
  "seller" varchar(160) not null primary key,
  "pct" numeric(5, 2) not null check ("pct" between 0 and 100),
  "updated_at" timestamptz not null default now()
);

create table if not exists commission_payouts (
  "id" uuid not null default gen_random_uuid() primary key,
  "seller" varchar(160) not null,
  "period" char(7) not null check ("period" ~ '^\d{4}-(0[1-9]|1[0-2])$'),
  "amount" numeric(14, 2) not null check ("amount" >= 0),
  "paid_at" timestamptz not null default now(),
  "paid_by_user_id" uuid references users("id") on delete set null,
  "paid_by_name" varchar(160) not null,
  unique ("seller", "period")
);
create index if not exists commission_payouts_idx_1 on commission_payouts ("period");

-- ───────── Inventario físico (conteos) ─────────
create table if not exists stock_counts (
  "id" uuid not null default gen_random_uuid() primary key,
  "code" varchar(24) not null unique,
  "warehouse_id" uuid not null references warehouses("id") on delete restrict,
  "status" varchar(8) not null default 'OPEN' check ("status" in ('OPEN', 'CLOSED')),
  "opened_by_user_id" uuid references users("id") on delete set null,
  "opened_by_name" varchar(160) not null,
  "opened_at" timestamptz not null default now(),
  "closed_at" timestamptz
);
create index if not exists stock_counts_idx_1 on stock_counts ("warehouse_id", "status");
create index if not exists stock_counts_idx_2 on stock_counts ("opened_at" desc);

create table if not exists stock_count_items (
  "id" uuid not null default gen_random_uuid() primary key,
  "count_id" uuid not null references stock_counts("id") on delete cascade,
  "sku" varchar(64) not null,
  "name" varchar(200) not null,
  "unit" varchar(24) not null,
  "system_qty" numeric(14, 3) not null,
  "counted_qty" numeric(14, 3) check ("counted_qty" is null or "counted_qty" >= 0),
  "unit_cost" numeric(14, 6) not null default 0,
  unique ("count_id", "sku")
);
create index if not exists stock_count_items_idx_1 on stock_count_items ("count_id");

-- Resumen por conteo (pendientes, diferencias y su valor al costo promedio) para listar sin traer los ítems.
create or replace view stock_count_summary with (security_invoker = true) as
select
  "count_id",
  count(*)::int as item_count,
  (count(*) filter (where "counted_qty" is null))::int as pending,
  (count(*) filter (where "counted_qty" is not null and "counted_qty" <> "system_qty"))::int as with_diff,
  coalesce(sum(case when "counted_qty" < "system_qty" then ("system_qty" - "counted_qty") * "unit_cost" end), 0)::numeric(16, 2) as missing_value,
  coalesce(sum(case when "counted_qty" > "system_qty" then ("counted_qty" - "system_qty") * "unit_cost" end), 0)::numeric(16, 2) as surplus_value
from stock_count_items
group by "count_id";

-- ───────── Hoja de ruta: confirmación de entrega por orden de venta ─────────
create table if not exists delivery_confirmations (
  "order_id" uuid not null primary key references business_orders("id") on delete cascade,
  "status" varchar(14) not null check ("status" in ('DELIVERED', 'NOT_DELIVERED')),
  "receiver" varchar(160),
  "reason" varchar(160),
  "note" text,
  "confirmed_at" timestamptz not null default now(),
  "confirmed_by_user_id" uuid references users("id") on delete set null,
  "confirmed_by_name" varchar(160) not null
);

-- ───────── Flujo de caja: saldo inicial escrito a mano (null = Caja + Bancos del sistema) ─────────
create table if not exists cashflow_settings (
  "id" smallint not null default 1 primary key check ("id" = 1),
  "opening_balance" numeric(14, 2),
  "updated_at" timestamptz not null default now()
);
insert into cashflow_settings ("id") values (1) on conflict ("id") do nothing;

-- ───────── updated_at automático (set_updated_at ya existe en 001_schema.sql) ─────────
drop trigger if exists trg_module_switches_updated_at on module_switches;
create trigger trg_module_switches_updated_at before update on module_switches for each row execute function set_updated_at();
drop trigger if exists trg_sales_goals_updated_at on sales_goals;
create trigger trg_sales_goals_updated_at before update on sales_goals for each row execute function set_updated_at();
drop trigger if exists trg_commission_settings_updated_at on commission_settings;
create trigger trg_commission_settings_updated_at before update on commission_settings for each row execute function set_updated_at();
drop trigger if exists trg_commission_rates_updated_at on commission_rates;
create trigger trg_commission_rates_updated_at before update on commission_rates for each row execute function set_updated_at();
drop trigger if exists trg_cashflow_settings_updated_at on cashflow_settings;
create trigger trg_cashflow_settings_updated_at before update on cashflow_settings for each row execute function set_updated_at();

-- ───────── Seguridad: solo el backend (service_role) lee y escribe ─────────
alter table module_switches enable row level security;
alter table sales_goals enable row level security;
alter table commission_settings enable row level security;
alter table commission_rates enable row level security;
alter table commission_payouts enable row level security;
alter table stock_counts enable row level security;
alter table stock_count_items enable row level security;
alter table delivery_confirmations enable row level security;
alter table cashflow_settings enable row level security;
revoke all on stock_count_summary from public, anon, authenticated;
grant select on stock_count_summary to service_role;

commit;