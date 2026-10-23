-- ============================================================================
-- 009 · Gráficos del Dashboard: ventas diarias y ventas por categoría
-- Ejecutar completo en el SQL Editor de Supabase. Es idempotente (se puede correr más de una vez).
--
-- Usa la misma regla que "Ventas" del Dashboard: solo lo FACTURADO (facturas y boletas no anuladas, de órdenes de
-- venta no anuladas), por fecha de emisión del comprobante. No toca dashboard_summary.
--
-- Rango (el mismo para los dos gráficos):
--   today / week -> últimos 7 días (incluido hoy)
--   month        -> del día 1 del mes hasta hoy
--   year         -> del 1 de enero hasta hoy
-- ============================================================================
create or replace function dashboard_charts(p_period text, p_tz text default 'America/Lima')
returns jsonb
language plpgsql
stable
set search_path = public
as $$
declare
  v_today date := (now() at time zone p_tz)::date;
  v_from date;
  v_daily jsonb;
  v_cat jsonb;
begin
  case p_period
    when 'today' then v_from := v_today - 6;
    when 'week'  then v_from := v_today - 6;
    when 'month' then v_from := date_trunc('month', v_today::timestamp)::date;
    when 'year'  then v_from := date_trunc('year', v_today::timestamp)::date;
    else raise exception 'invalid period: %', p_period;
  end case;

  -- Ventas por día (los días sin ventas aparecen en 0)
  select coalesce(jsonb_agg(jsonb_build_object('date', d.day, 'total', coalesce(s.total, 0), 'count', coalesce(s.cnt, 0)) order by d.day), '[]'::jsonb)
  into v_daily
  from (select v_from + n as day from generate_series(0, v_today - v_from) as n) d
  left join (
    select si.issued_at as day, sum(si.total) as total, count(*) as cnt
    from sales_invoices si
    join business_orders o on o.id = si.order_id and o.kind = 'SALE' and not o.is_cancelled
    where si.document_type in ('INVOICE', 'RECEIPT') and si.tax_status <> 'VOIDED'
      and si.issued_at >= v_from and si.issued_at <= v_today
    group by si.issued_at
  ) s on s.day = d.day;

  -- Ventas por categoría de producto (total con IGV, de mayor a menor)
  select coalesce(jsonb_agg(jsonb_build_object('category', c.name, 'total', c.total, 'units', c.units) order by c.total desc), '[]'::jsonb)
  into v_cat
  from (
    select coalesce(pc.name, 'Sin categoría') as name,
           round(sum(l.quantity * l.unit_price * (1 + o.tax_rate)), 2) as total,
           sum(l.quantity) as units
    from sales_invoices si
    join business_orders o on o.id = si.order_id and o.kind = 'SALE' and not o.is_cancelled
    join order_lines l on l.order_id = o.id
    left join products p on p.id = l.product_id
    left join product_categories pc on pc.id = p.category_id
    where si.document_type in ('INVOICE', 'RECEIPT') and si.tax_status <> 'VOIDED'
      and si.issued_at >= v_from and si.issued_at <= v_today
    group by 1
  ) c
  where c.total > 0;

  return jsonb_build_object('from', v_from, 'to', v_today, 'dailySales', v_daily, 'salesByCategory', v_cat);
end;
$$;

-- Solo el backend (service_role) puede ejecutarla; la anon key pública de Supabase no.
revoke all on function dashboard_charts(text, text) from public, anon, authenticated;
grant execute on function dashboard_charts(text, text) to service_role;