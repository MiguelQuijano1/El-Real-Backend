-- database/012_salida_a_ruta_y_pago.sql
-- Ejecutar con: psql $DATABASE_URL -f database/012_salida_a_ruta_y_pago.sql
-- O aplicar manualmente en Supabase › SQL Editor.

begin;

-- Correos: además de despacho y entrega, el aviso de pago recibido.
alter table email_notifications_log drop constraint if exists email_notifications_log_event_check;
alter table email_notifications_log
    add constraint email_notifications_log_event_check
    check (event in ('dispatched', 'delivered', 'not_delivered', 'paid'));

-- Salida a ruta: cuándo salió cada vehículo con las entregas de un día (una vez por vehículo y día).
create table if not exists route_departures (
    vehicle_plate      varchar(20)  not null,
    delivery_day       date         not null,
    started_at         timestamptz  not null default now(),
    started_by_user_id uuid         references users(id) on delete set null,
    started_by_name    varchar(160) not null,
    primary key (vehicle_plate, delivery_day)
);

commit;
