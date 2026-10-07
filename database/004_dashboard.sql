-- Resumen del Dashboard calculado en la base (una sola consulta, sin traer miles de filas al backend).
-- p_period: 'today' | 'week' | 'month' | 'year'. Los límites de cada periodo se calculan en p_tz.

create or replace function dashboard_summary(p_period text, p_tz text default 'America/Lima')
returns jsonb
language plpgsql
stable
set search_path = public
as $$
declare
  v_today date := (now() at time zone p_tz)::date;
  v_start date;
  v_end date;
  v_prev date;
  v_cur_from timestamptz;
  v_cur_to timestamptz;
  v_prev_from timestamptz;
  v_year_from timestamptz := now() - interval '12 months';

  v_sales_cur numeric; v_sales_cnt int; v_sales_prev numeric;
  v_purch_cur numeric; v_purch_cnt int; v_purch_prev numeric; v_to_receive int;
  v_sub numeric; v_cost numeric;
  v_crit_count int; v_crit jsonb;
  v_rec_count int; v_rec_overdue int; v_rec_balance numeric; v_rec_alerts jsonb;
  v_pay_count int; v_pay_week int; v_pay_overdue int; v_pay_balance numeric; v_pay_alerts jsonb;
  v_cogs numeric; v_inv numeric; v_turn jsonb := null;
  v_latest jsonb; v_sales_total_count int;
begin
  case p_period
    when 'today' then v_start := v_today; v_end := v_today + 1; v_prev := v_today - 1;
    when 'week'  then v_start := date_trunc('week', v_today::timestamp)::date; v_end := v_start + 7; v_prev := v_start - 7;
    when 'month' then v_start := date_trunc('month', v_today::timestamp)::date; v_end := (v_start + interval '1 month')::date; v_prev := (v_start - interval '1 month')::date;
    when 'year'  then v_start := date_trunc('year', v_today::timestamp)::date; v_end := (v_start + interval '1 year')::date; v_prev := (v_start - interval '1 year')::date;
    else raise exception 'invalid period: %', p_period;
  end case;
  v_cur_from := v_start::timestamp at time zone p_tz;
  v_cur_to := v_end::timestamp at time zone p_tz;
  v_prev_from := v_prev::timestamp at time zone p_tz;

  -- Ventas y compras (total con IGV por orden, órdenes anuladas excluidas)
  select
    coalesce(sum(t) filter (where kind = 'SALE' and created_at >= v_cur_from and created_at < v_cur_to), 0),
    count(*)        filter (where kind = 'SALE' and created_at >= v_cur_from and created_at < v_cur_to),
    coalesce(sum(t) filter (where kind = 'SALE' and created_at >= v_prev_from and created_at < v_cur_from), 0),
    coalesce(sum(t) filter (where kind = 'PURCHASE' and created_at >= v_cur_from and created_at < v_cur_to), 0),
    count(*)        filter (where kind = 'PURCHASE' and created_at >= v_cur_from and created_at < v_cur_to),
    coalesce(sum(t) filter (where kind = 'PURCHASE' and created_at >= v_prev_from and created_at < v_cur_from), 0)
  into v_sales_cur, v_sales_cnt, v_sales_prev, v_purch_cur, v_purch_cnt, v_purch_prev
  from (
    select o.kind, o.created_at, round(coalesce(sum(l.quantity * l.unit_price), 0) * (1 + o.tax_rate), 2) as t
    from business_orders o
    left join order_lines l on l.order_id = o.id
    where not o.is_cancelled and o.created_at >= v_prev_from and o.created_at < v_cur_to
    group by o.id
  ) s;

  select count(*) into v_to_receive from business_orders where kind = 'PURCHASE' and not is_cancelled and current_step = 2;

  -- Utilidad bruta del periodo: precio de venta vs costo promedio (si el producto aún no tiene costo, no suma margen)
  select coalesce(sum(l.quantity * l.unit_price), 0),
         coalesce(sum(l.quantity * case when coalesce(p.average_cost, 0) > 0 then p.average_cost else l.unit_price end), 0)
  into v_sub, v_cost
  from business_orders o
  join order_lines l on l.order_id = o.id
  left join products p on p.id = l.product_id
  where o.kind = 'SALE' and not o.is_cancelled and o.created_at >= v_cur_from and o.created_at < v_cur_to;

  -- Stock por producto y almacén a partir del kardex
  select count(*),
         coalesce(jsonb_agg(x.j order by x.rn) filter (where x.rn <= 10), '[]'::jsonb)
  into v_crit_count, v_crit
  from (
    select jsonb_build_object('sku', p.sku, 'name', p.name, 'warehouse', w.name, 'quantity', st.qty, 'minimum', p.minimum_stock) as j,
           row_number() over (order by st.qty / nullif(p.minimum_stock, 0) nulls first) as rn
    from (select product_id, warehouse_id, sum(quantity_delta) as qty from inventory_movements group by 1, 2) st
    join products p on p.id = st.product_id and p.status = 'ACTIVE'
    join warehouses w on w.id = st.warehouse_id
    where st.qty <= 0 or st.qty < p.minimum_stock
  ) x;

  -- Cuentas por cobrar (facturas y boletas no anuladas, menos cobros y notas de crédito)
  with r as (
    select si.id, si.series || '-' || si.number as doc, c.legal_name, si.due_at,
           si.total
             - coalesce((select sum(t.signed_amount) from treasury_movements t where t.sales_invoice_id = si.id and t.movement_type = 'COLLECTION'), 0)
             - coalesce((select sum(cn.total) from sales_invoices cn where cn.original_invoice_id = si.id and cn.document_type = 'CREDIT_NOTE' and cn.tax_status <> 'VOIDED'), 0) as balance
    from sales_invoices si
    join customers c on c.id = si.customer_id
    where si.document_type in ('INVOICE', 'RECEIPT') and si.tax_status <> 'VOIDED'
  )
  select count(*) filter (where balance > 0.005),
         count(*) filter (where balance > 0.005 and due_at < v_today),
         coalesce(sum(balance) filter (where balance > 0.005), 0),
         coalesce((select jsonb_agg(jsonb_build_object('doc', doc, 'customer', legal_name, 'dueAt', due_at, 'daysOverdue', v_today - due_at, 'balance', round(balance, 2)))
                   from (select * from r where balance > 0.005 and due_at < v_today order by due_at limit 10) o), '[]'::jsonb)
  into v_rec_count, v_rec_overdue, v_rec_balance, v_rec_alerts
  from r;

  -- Cuentas por pagar (facturas de proveedor menos pagos)
  with p as (
    select si.id, si.series_number as doc, s.legal_name, si.due_at,
           si.total - coalesce((select sum(-t.signed_amount) from treasury_movements t where t.supplier_invoice_id = si.id and t.movement_type = 'PAYMENT'), 0) as balance
    from supplier_invoices si
    join suppliers s on s.id = si.supplier_id
  )
  select count(*) filter (where balance > 0.005),
         count(*) filter (where balance > 0.005 and due_at >= v_today and due_at <= v_today + 7),
         count(*) filter (where balance > 0.005 and due_at < v_today),
         coalesce(sum(balance) filter (where balance > 0.005), 0),
         coalesce((select jsonb_agg(jsonb_build_object('doc', doc, 'supplier', legal_name, 'dueAt', due_at, 'daysOverdue', v_today - due_at, 'balance', round(balance, 2)))
                   from (select * from p where balance > 0.005 and due_at <= v_today + 7 order by due_at limit 10) o), '[]'::jsonb)
  into v_pay_count, v_pay_week, v_pay_overdue, v_pay_balance, v_pay_alerts
  from p;

  -- Rotación de inventario: costo de lo vendido en 12 meses / valor del inventario actual
  select coalesce(sum(l.quantity * p.average_cost), 0) into v_cogs
  from business_orders o
  join order_lines l on l.order_id = o.id
  join products p on p.id = l.product_id
  where o.kind = 'SALE' and not o.is_cancelled and o.created_at >= v_year_from;

  select coalesce(sum(st.qty * p.average_cost), 0) into v_inv
  from (select product_id, sum(quantity_delta) as qty from inventory_movements group by 1) st
  join products p on p.id = st.product_id;

  if v_inv > 0 and v_cogs > 0 then
    v_turn := jsonb_build_object('times', round(v_cogs / v_inv, 2), 'days', round(365 / (v_cogs / v_inv)));
  end if;

  -- Últimas ventas con su estado de cobro
  select count(*) into v_sales_total_count from business_orders where kind = 'SALE';

  select coalesce(jsonb_agg(jsonb_build_object(
           'code', q.code, 'partyName', q.party_name, 'partyTaxId', q.party_tax_id,
           'total', q.total, 'status', q.status, 'createdAt', q.created_at) order by q.created_at desc), '[]'::jsonb)
  into v_latest
  from (
    select o.code, o.party_name, o.party_tax_id, o.created_at,
           round(coalesce((select sum(l.quantity * l.unit_price) from order_lines l where l.order_id = o.id), 0) * (1 + o.tax_rate), 2) as total,
           case
             when o.is_cancelled then 'CANCELLED'
             when inv.id is null then 'PENDING'
             when coalesce(paid.amount, 0) >= inv.total - 0.005 then 'PAID'
             when inv.due_at < v_today then 'OVERDUE'
             else 'PENDING'
           end as status
    from business_orders o
    left join sales_invoices inv on inv.order_id = o.id and inv.tax_status <> 'VOIDED'
    left join lateral (
      select sum(t.signed_amount) as amount from treasury_movements t
      where t.sales_invoice_id = inv.id and t.movement_type = 'COLLECTION'
    ) paid on true
    where o.kind = 'SALE'
    order by o.created_at desc
    limit 50
  ) q;

  return jsonb_build_object(
    'period', p_period,
    'sales', jsonb_build_object('total', v_sales_cur, 'previousTotal', v_sales_prev, 'count', v_sales_cnt),
    'purchases', jsonb_build_object('total', v_purch_cur, 'previousTotal', v_purch_prev, 'count', v_purch_cnt, 'toReceive', v_to_receive),
    'grossProfit', jsonb_build_object('amount', round(v_sub - v_cost, 2), 'margin', case when v_sub > 0 then round((v_sub - v_cost) / v_sub, 4) else 0 end),
    'criticalStock', jsonb_build_object('count', v_crit_count, 'items', v_crit),
    'receivables', jsonb_build_object('count', v_rec_count, 'overdue', v_rec_overdue, 'balance', v_rec_balance, 'overdueItems', v_rec_alerts),
    'payables', jsonb_build_object('count', v_pay_count, 'dueThisWeek', v_pay_week, 'overdue', v_pay_overdue, 'balance', v_pay_balance, 'dueItems', v_pay_alerts),
    'turnover', v_turn,
    'latestSales', v_latest,
    'salesTotalCount', v_sales_total_count
  );
end;
$$;

-- Solo el backend (service_role) puede ejecutarla; la anon key pública de Supabase no.
revoke all on function dashboard_summary(text, text) from public, anon, authenticated;
grant execute on function dashboard_summary(text, text) to service_role;