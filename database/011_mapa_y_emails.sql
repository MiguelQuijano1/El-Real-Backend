-- database/011_mapa_y_emails.sql
-- Ejecutar con: psql $DATABASE_URL -f database/011_mapa_y_emails.sql
-- O aplicar manualmente en Supabase › SQL Editor.

begin;

-- ── Coordenadas para geocodificación ──────────────────────────────────────────

-- Clientes: lat/lng cacheadas (Nominatim)
alter table customers
    add column if not exists lat double precision,
    add column if not exists lng double precision;

-- Almacenes: lat/lng (address y district ya existen)
alter table warehouses
    add column if not exists lat double precision,
    add column if not exists lng double precision;

-- Vehículos: zonas de reparto (array de distritos permitidos, vacío = sin restricción)
alter table vehicles
    add column if not exists zones text[] not null default '{}';

-- Órdenes: coordenadas de la dirección de entrega (cacheadas al despachar)
alter table business_orders
    add column if not exists delivery_lat double precision,
    add column if not exists delivery_lng double precision;

-- ── SMTP ──────────────────────────────────────────────────────────────────────

create table if not exists smtp_settings (
    id           integer primary key default 1 check (id = 1),  -- fila única
    host         varchar(254) not null default '',
    port         integer      not null default 587,
    username     varchar(254) not null default '',
    password     text         not null default '',
    from_address varchar(254) not null default '',
    use_tls      boolean      not null default true,
    enabled      boolean      not null default false,
    updated_at   timestamptz  not null default now()
);

-- Fila única inicial (no-op si ya existe)
insert into smtp_settings (id) values (1) on conflict (id) do nothing;

-- ── Log de emails enviados ────────────────────────────────────────────────────

create table if not exists email_notifications_log (
    id         uuid        primary key default gen_random_uuid(),
    order_id   uuid        references business_orders(id) on delete set null,
    event      varchar(20) not null check (event in ('dispatched', 'delivered', 'not_delivered')),
    to_email   varchar(254) not null,
    sent_at    timestamptz not null default now(),
    error      text        -- null = enviado OK
);

create index if not exists email_log_idx_1 on email_notifications_log (order_id);
create index if not exists email_log_idx_2 on email_notifications_log (sent_at desc);

commit;
