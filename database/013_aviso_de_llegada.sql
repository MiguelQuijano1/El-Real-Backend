-- database/013_aviso_de_llegada.sql
-- Ejecutar con: psql $DATABASE_URL -f database/013_aviso_de_llegada.sql
-- O aplicar manualmente en Supabase › SQL Editor.

begin;

-- Correos: aviso de que el vehículo llegó a la dirección del cliente.
alter table email_notifications_log drop constraint if exists email_notifications_log_event_check;
alter table email_notifications_log
    add constraint email_notifications_log_event_check
    check (event in ('dispatched', 'delivered', 'not_delivered', 'paid', 'arrived'));

commit;
