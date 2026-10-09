-- ============================================================================
-- 006 · Precios por proveedor + Compras facturadas en el Dashboard
-- Ejecutar completo en el SQL Editor de Supabase. Es idempotente (se puede correr más de una vez).
-- ============================================================================

-- Lista de precios por proveedor: qué producto vende cada proveedor y a cuánto (sin IGV).
create table if not exists supplier_products (
  "id" uuid not null default gen_random_uuid() primary key,
  "supplier_id" uuid not null references suppliers("id") on delete cascade,
  "product_id" uuid not null references products("id") on delete cascade,
  "supplier_sku" varchar(64),
  "unit_cost" numeric(14, 6) not null check (unit_cost >= 0),
  "notes" text,
  "status" record_status not null default 'ACTIVE',
  "created_at" timestamptz not null default now(),
  "updated_at" timestamptz not null default now(),
  unique ("supplier_id", "product_id")
);
create index if not exists supplier_products_idx_1 on supplier_products ("product_id");

drop trigger if exists trg_supplier_products_updated_at on supplier_products;
create trigger trg_supplier_products_updated_at before update on supplier_products for each row execute function set_updated_at();

-- Igual que el resto de tablas: RLS activo sin políticas; solo el backend (service_role) accede.
alter table supplier_products enable row level security;

-- ---------------------------------------------------------------------------
-- Reemplaza dashboard_summary de 005: ahora también "Compras" cuenta solo lo FACTURADO por el proveedor (factura registrada),
-- igual que "Ventas" cuenta solo lo facturado. Es idempotente.
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

  -- Compras: solo lo FACTURADO por el proveedor (factura registrada en la orden de compra), por fecha de emisión de la factura.
  -- Una orden por aprobar o por recibir todavía no es una compra. Total con IGV.
  select
    coalesce(sum(t) filter (where issued_at >= v_start and issued_at < v_end), 0),
    count(*)        filter (where issued_at >= v_start and issued_at < v_end),
    coalesce(sum(t) filter (where issued_at >= v_prev  and issued_at < v_start), 0)
  into v_purch_cur, v_purch_cnt, v_purch_prev
  from (
    select si.issued_at, round(coalesce(sum(l.quantity * l.unit_price), 0) * (1 + o.tax_rate), 2) as t
    from supplier_invoices si
    join business_orders o on o.id = si.order_id and o.kind = 'PURCHASE' and not o.is_cancelled
    left join order_lines l on l.order_id = o.id
    where si.issued_at >= v_prev and si.issued_at < v_end
    group by si.id, o.id
  ) s;

  -- Ventas: solo lo FACTURADO (facturas y boletas no anuladas), por fecha de emisión del comprobante.
  -- Una orden confirmada o despachada que aún no tiene comprobante no cuenta como venta.
  select
    coalesce(sum(si.total) filter (where si.issued_at >= v_start and si.issued_at < v_end), 0),
    count(*)               filter (where si.issued_at >= v_start and si.issued_at < v_end),
    coalesce(sum(si.total) filter (where si.issued_at >= v_prev  and si.issued_at < v_start), 0)
  into v_sales_cur, v_sales_cnt, v_sales_prev
  from sales_invoices si
  join business_orders o on o.id = si.order_id and o.kind = 'SALE' and not o.is_cancelled
  where si.document_type in ('INVOICE', 'RECEIPT') and si.tax_status <> 'VOIDED'
    and si.issued_at >= v_prev and si.issued_at < v_end;

  select count(*) into v_to_receive from business_orders where kind = 'PURCHASE' and not is_cancelled and current_step = 2;

  -- Utilidad bruta del periodo: solo órdenes facturadas (misma regla que Ventas), precio de venta vs costo promedio
  -- (si el producto aún no tiene costo, no suma margen). Las notas de crédito no se descuentan aquí.
  select coalesce(sum(l.quantity * l.unit_price), 0),
         coalesce(sum(l.quantity * case when coalesce(p.average_cost, 0) > 0 then p.average_cost else l.unit_price end), 0)
  into v_sub, v_cost
  from sales_invoices si
  join business_orders o on o.id = si.order_id and o.kind = 'SALE' and not o.is_cancelled
  join order_lines l on l.order_id = o.id
  left join products p on p.id = l.product_id
  where si.document_type in ('INVOICE', 'RECEIPT') and si.tax_status <> 'VOIDED'
    and si.issued_at >= v_start and si.issued_at < v_end;

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
  from sales_invoices si
  join business_orders o on o.id = si.order_id and o.kind = 'SALE' and not o.is_cancelled
  join order_lines l on l.order_id = o.id
  join products p on p.id = l.product_id
  where si.document_type in ('INVOICE', 'RECEIPT') and si.tax_status <> 'VOIDED'
    and si.issued_at >= (v_year_from at time zone p_tz)::date;

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