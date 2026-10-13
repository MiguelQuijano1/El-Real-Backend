-- Las órdenes de compra ya no piden un paso de "aprobar orden": nacen en el paso 2 (listas para recepción en Kardex).
-- Esta migración lleva al paso 2 las órdenes de compra que quedaron en el paso 1 ("Por aprobar") con el esquema anterior.
-- No toca órdenes anuladas ni órdenes que ya avanzaron. Es idempotente: se puede ejecutar más de una vez.
begin;

insert into order_step_events (order_id, step_no, completed_by_user_id, completed_at, generated_reference, note)
select o.id, 1, o.created_by_user_id, now(), o.code, 'Migración: orden sin aprobación previa'
from business_orders o
where o.kind = 'PURCHASE' and o.current_step = 1 and not o.is_cancelled
  and not exists (select 1 from order_step_events e where e.order_id = o.id and e.step_no = 1);

update business_orders
set current_step = 2
where kind = 'PURCHASE' and current_step = 1 and not is_cancelled;

commit;
