-- Cotizaciones sin cliente: se cotiza sin saber aún quién comprará; el cliente se asigna al convertir
-- la cotización en orden de venta. Idempotente y sin pérdida de datos.
alter table quotations alter column customer_id drop not null;
