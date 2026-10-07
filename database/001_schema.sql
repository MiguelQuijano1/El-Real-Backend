-- Generado a partir de backend/prisma/schema.prisma (NestJS) · esquema idéntico

create extension if not exists citext;
create extension if not exists pgcrypto;

create type role_type as enum ('SYSTEM', 'CUSTOM');
create type user_status as enum ('INVITED', 'ACTIVE', 'LOCKED', 'INACTIVE');
create type identification_type as enum ('RUC', 'DNI', 'CE');
create type record_status as enum ('ACTIVE', 'INACTIVE');
create type warehouse_type as enum ('MAIN', 'SECONDARY', 'TRANSIT');
create type quotation_status as enum ('DRAFT', 'SENT', 'APPROVED', 'CONVERTED', 'REJECTED');
create type purchase_request_status as enum ('PENDING', 'APPROVED', 'ATTENDED', 'REJECTED');
create type request_priority as enum ('HIGH', 'MEDIUM', 'LOW');
create type business_order_kind as enum ('SALE', 'PURCHASE');
create type dispatch_reason as enum ('SALE', 'SALE_CONFIRMATION_PENDING');
create type goods_condition as enum ('CONFORMING', 'OBSERVATIONS');
create type sales_document_type as enum ('INVOICE', 'RECEIPT', 'CREDIT_NOTE');
create type tax_submission_status as enum ('PENDING', 'ACCEPTED', 'REJECTED', 'VOIDED');
create type supplier_invoice_validation_status as enum ('MATCHES_ORDER_RECEIPT', 'HAS_DIFFERENCES');
create type transfer_status as enum ('PENDING', 'IN_TRANSIT', 'RECEIVED', 'CANCELLED');
create type adjustment_type as enum ('LOSS', 'SURPLUS', 'EXPIRY', 'CORRECTION');
create type inventory_movement_type as enum ('RECEIPT', 'DISPATCH', 'TRANSFER', 'ADJUSTMENT');
create type movement_leg as enum ('OUT', 'IN');
create type treasury_channel as enum ('CASH', 'BANK');
create type treasury_movement_type as enum ('COLLECTION', 'PAYMENT', 'OPENING', 'BANK_FEE', 'MANUAL_INCOME', 'MANUAL_EXPENSE');
create type payment_method as enum ('TRANSFER', 'DEPOSIT', 'CHEQUE', 'CASH', 'YAPE', 'PLIN', 'POS');
create type reconciliation_status as enum ('PENDING', 'RECONCILED');
create type document_category as enum ('INVOICE', 'CONTRACT', 'PURCHASE_ORDER', 'DISPATCH_GUIDE', 'OTHER', 'ATTACHMENT');
create type audit_action as enum ('LOGIN', 'CREATE', 'EDIT', 'DELETE', 'EXPORT', 'DOWNLOAD');
create type audit_result as enum ('SUCCESS', 'FAILURE', 'DENIED');
create type report_frequency as enum ('DAILY', 'WEEKLY', 'MONTHLY');
create type cost_method as enum ('WEIGHTED_AVERAGE');

create table roles (
  "id" uuid not null default gen_random_uuid() primary key,
  "code" varchar(40) not null unique,
  "name" varchar(80) not null unique,
  "description" text,
  "role_type" role_type not null default 'CUSTOM',
  "is_active" boolean not null default true,
  "created_at" timestamptz not null default now(),
  "updated_at" timestamptz not null default now()
);

create table permissions (
  "id" uuid not null default gen_random_uuid() primary key,
  "module_key" varchar(80) not null,
  "action_key" varchar(16) not null,
  unique ("module_key", "action_key")
);

create table role_permissions (
  "role_id" uuid not null,
  "permission_id" uuid not null,
  "granted" boolean not null default false,
  "updated_at" timestamptz not null default now(),
  primary key ("role_id", "permission_id"),
  foreign key ("role_id") references roles("id") on delete cascade,
  foreign key ("permission_id") references permissions("id") on delete cascade
);
create index role_permissions_idx_1 on role_permissions ("permission_id");

create table users (
  "id" uuid not null default gen_random_uuid() primary key,
  "role_id" uuid not null,
  "full_name" varchar(160) not null,
  "email" citext not null unique,
  "password_hash" text,
  "area" varchar(60),
  "status" user_status not null default 'INVITED',
  "mfa_enabled" boolean not null default false,
  "failed_login_attempts" smallint not null default 0,
  "locked_until" timestamptz,
  "last_login_at" timestamptz,
  "created_at" timestamptz not null default now(),
  "updated_at" timestamptz not null default now(),
  foreign key ("role_id") references roles("id") on delete restrict
);
create index users_idx_1 on users ("role_id", "status");

create table auth_sessions (
  "id" uuid not null default gen_random_uuid() primary key,
  "user_id" uuid not null,
  "token_hash" text not null unique,
  "persistent" boolean not null default true,
  "issued_at" timestamptz not null default now(),
  "last_seen_at" timestamptz,
  "expires_at" timestamptz not null,
  "revoked_at" timestamptz,
  "ip" inet,
  "user_agent" text,
  foreign key ("user_id") references users("id") on delete cascade
);
create index auth_sessions_idx_1 on auth_sessions ("user_id", "revoked_at", "expires_at");

create table customers (
  "id" uuid not null default gen_random_uuid() primary key,
  "code" varchar(20) not null unique,
  "legal_name" varchar(200) not null,
  "document_type" identification_type not null,
  "document_number" varchar(24) not null,
  "contact_name" varchar(160),
  "phone" varchar(40),
  "billing_email" varchar(254),
  "fiscal_address" text,
  "payment_terms_days" smallint not null default 0,
  "credit_limit" numeric(14, 2) not null default 0,
  "salesperson_user_id" uuid,
  "status" record_status not null default 'ACTIVE',
  "created_at" timestamptz not null default now(),
  "updated_at" timestamptz not null default now(),
  unique ("document_type", "document_number"),
  foreign key ("salesperson_user_id") references users("id") on delete set null
);
create index customers_idx_1 on customers ("status");
create index customers_idx_2 on customers ("salesperson_user_id");

create table suppliers (
  "id" uuid not null default gen_random_uuid() primary key,
  "code" varchar(20) not null unique,
  "legal_name" varchar(200) not null,
  "tax_id" varchar(24) not null unique,
  "contact_name" varchar(160),
  "phone" varchar(40),
  "email" varchar(254),
  "address" text,
  "business_category" varchar(80),
  "payment_terms_days" smallint not null default 0,
  "bank_details_label" text,
  "status" record_status not null default 'ACTIVE',
  "created_at" timestamptz not null default now(),
  "updated_at" timestamptz not null default now()
);

create table product_categories (
  "id" uuid not null default gen_random_uuid() primary key,
  "code" varchar(20) not null unique,
  "name" varchar(120) not null unique,
  "description" text,
  "parent_category_id" uuid,
  "target_margin_pct" numeric(5, 2),
  "status" record_status not null default 'ACTIVE',
  foreign key ("parent_category_id") references product_categories("id") on delete restrict
);
create index product_categories_idx_1 on product_categories ("parent_category_id");

create table products (
  "id" uuid not null default gen_random_uuid() primary key,
  "sku" varchar(64) not null unique,
  "name" varchar(200) not null,
  "category_id" uuid,
  "unit" varchar(24) not null,
  "sale_price" numeric(14, 6) not null,
  "average_cost" numeric(14, 6) not null default 0,
  "minimum_stock" numeric(14, 3) not null default 0,
  "primary_supplier_id" uuid,
  "taxable" boolean not null default true,
  "status" record_status not null default 'ACTIVE',
  "created_at" timestamptz not null default now(),
  "updated_at" timestamptz not null default now(),
  foreign key ("category_id") references product_categories("id") on delete set null,
  foreign key ("primary_supplier_id") references suppliers("id") on delete set null
);
create index products_idx_1 on products ("category_id", "status");
create index products_idx_2 on products ("primary_supplier_id");

create table warehouses (
  "id" uuid not null default gen_random_uuid() primary key,
  "code" varchar(20) not null unique,
  "name" varchar(120) not null unique,
  "warehouse_type" warehouse_type not null,
  "address" text,
  "district" varchar(100),
  "manager_user_id" uuid,
  "status" record_status not null default 'ACTIVE',
  foreign key ("manager_user_id") references users("id") on delete set null
);
create index warehouses_idx_1 on warehouses ("status");
create index warehouses_idx_2 on warehouses ("manager_user_id");

create table drivers (
  "id" uuid not null default gen_random_uuid() primary key,
  "code" varchar(20) not null unique,
  "full_name" varchar(160) not null,
  "national_id" varchar(24) not null unique,
  "phone" varchar(40),
  "license_number" varchar(40) not null unique,
  "license_category" varchar(16) not null,
  "is_company_driver" boolean not null default true,
  "carrier_supplier_id" uuid,
  "status" record_status not null default 'ACTIVE',
  "created_at" timestamptz not null default now(),
  "updated_at" timestamptz not null default now(),
  foreign key ("carrier_supplier_id") references suppliers("id") on delete restrict
);
create index drivers_idx_1 on drivers ("carrier_supplier_id", "status");

create table vehicles (
  "id" uuid not null default gen_random_uuid() primary key,
  "plate" varchar(16) not null unique,
  "vehicle_type" varchar(32) not null,
  "brand" varchar(80),
  "model" varchar(80),
  "capacity" numeric(12, 3),
  "capacity_unit" varchar(12),
  "is_company_vehicle" boolean not null default true,
  "carrier_supplier_id" uuid,
  "default_driver_id" uuid,
  "status" record_status not null default 'ACTIVE',
  "created_at" timestamptz not null default now(),
  "updated_at" timestamptz not null default now(),
  foreign key ("carrier_supplier_id") references suppliers("id") on delete restrict,
  foreign key ("default_driver_id") references drivers("id") on delete set null
);
create index vehicles_idx_1 on vehicles ("carrier_supplier_id", "status");
create index vehicles_idx_2 on vehicles ("default_driver_id");

create table quotations (
  "id" uuid not null default gen_random_uuid() primary key,
  "code" varchar(24) not null unique,
  "customer_id" uuid not null,
  "seller_user_id" uuid,
  "created_at" timestamptz not null default now(),
  "valid_until" date,
  "payment_terms_days" smallint not null default 0,
  "warehouse_id" uuid,
  "accepted_contact_snapshot" varchar(160),
  "status" quotation_status not null default 'DRAFT',
  "rejected_reason" text,
  "updated_at" timestamptz not null default now(),
  foreign key ("customer_id") references customers("id") on delete restrict,
  foreign key ("seller_user_id") references users("id") on delete set null,
  foreign key ("warehouse_id") references warehouses("id") on delete set null
);
create index quotations_idx_1 on quotations ("customer_id", "created_at");
create index quotations_idx_2 on quotations ("status", "valid_until");
create index quotations_idx_3 on quotations ("seller_user_id");

create table quotation_lines (
  "id" uuid not null default gen_random_uuid() primary key,
  "quotation_id" uuid not null,
  "line_no" smallint not null,
  "product_id" uuid,
  "sku_snapshot" varchar(64) not null,
  "name_snapshot" varchar(200) not null,
  "unit_snapshot" varchar(24) not null,
  "quantity" numeric(14, 3) not null,
  "unit_price" numeric(14, 6) not null,
  "created_at" timestamptz not null default now(),
  unique ("quotation_id", "line_no"),
  foreign key ("quotation_id") references quotations("id") on delete cascade,
  foreign key ("product_id") references products("id") on delete set null
);
create index quotation_lines_idx_1 on quotation_lines ("product_id");

create table purchase_requests (
  "id" uuid not null default gen_random_uuid() primary key,
  "code" varchar(24) not null unique,
  "requested_by_user_id" uuid not null,
  "requesting_area" varchar(60) not null,
  "reason" text not null,
  "priority" request_priority not null,
  "required_by" date,
  "warehouse_id" uuid,
  "suggested_supplier_id" uuid,
  "status" purchase_request_status not null default 'PENDING',
  "reviewed_by_user_id" uuid,
  "reviewed_at" timestamptz,
  "rejection_reason" text,
  "created_at" timestamptz not null default now(),
  "updated_at" timestamptz not null default now(),
  foreign key ("requested_by_user_id") references users("id") on delete restrict,
  foreign key ("reviewed_by_user_id") references users("id") on delete set null,
  foreign key ("warehouse_id") references warehouses("id") on delete set null,
  foreign key ("suggested_supplier_id") references suppliers("id") on delete set null
);
create index purchase_requests_idx_1 on purchase_requests ("status", "required_by");
create index purchase_requests_idx_2 on purchase_requests ("requested_by_user_id", "created_at");
create index purchase_requests_idx_3 on purchase_requests ("reviewed_by_user_id");

create table purchase_request_lines (
  "id" uuid not null default gen_random_uuid() primary key,
  "purchase_request_id" uuid not null,
  "line_no" smallint not null,
  "product_id" uuid,
  "name_snapshot" varchar(200) not null,
  "sku_snapshot" varchar(64),
  "unit_snapshot" varchar(24) not null,
  "quantity" numeric(14, 3) not null,
  "estimated_unit_cost" numeric(14, 6) not null,
  unique ("purchase_request_id", "line_no"),
  foreign key ("purchase_request_id") references purchase_requests("id") on delete cascade,
  foreign key ("product_id") references products("id") on delete set null
);
create index purchase_request_lines_idx_1 on purchase_request_lines ("product_id");

create table business_orders (
  "id" uuid not null default gen_random_uuid() primary key,
  "code" varchar(24) not null unique,
  "kind" business_order_kind not null,
  "customer_id" uuid,
  "supplier_id" uuid,
  "quotation_id" uuid unique,
  "purchase_request_id" uuid unique,
  "warehouse_id" uuid not null,
  "seller_user_id" uuid,
  "created_by_user_id" uuid,
  "party_name" varchar(200) not null,
  "party_tax_id" varchar(24),
  "party_address" text,
  "party_contact" varchar(200),
  "party_email" varchar(254),
  "payment_terms_days" smallint not null default 0,
  "tax_rate" numeric(5, 4) not null default 0.18,
  "current_step" smallint not null default 1,
  "is_cancelled" boolean not null default false,
  "cancelled_at" timestamptz,
  "cancelled_by_user_id" uuid,
  "created_at" timestamptz not null default now(),
  "updated_at" timestamptz not null default now(),
  foreign key ("customer_id") references customers("id") on delete restrict,
  foreign key ("supplier_id") references suppliers("id") on delete restrict,
  foreign key ("quotation_id") references quotations("id") on delete restrict,
  foreign key ("purchase_request_id") references purchase_requests("id") on delete restrict,
  foreign key ("warehouse_id") references warehouses("id") on delete restrict,
  foreign key ("seller_user_id") references users("id") on delete set null,
  foreign key ("created_by_user_id") references users("id") on delete set null,
  foreign key ("cancelled_by_user_id") references users("id") on delete set null
);
create index business_orders_idx_1 on business_orders ("kind", "created_at");
create index business_orders_idx_2 on business_orders ("customer_id", "created_at");
create index business_orders_idx_3 on business_orders ("supplier_id", "created_at");
create index business_orders_idx_4 on business_orders ("warehouse_id", "current_step");
create index business_orders_idx_5 on business_orders ("seller_user_id");

create table order_lines (
  "id" uuid not null default gen_random_uuid() primary key,
  "order_id" uuid not null,
  "line_no" smallint not null,
  "product_id" uuid,
  "sku_snapshot" varchar(64) not null,
  "name_snapshot" varchar(200) not null,
  "unit_snapshot" varchar(24) not null,
  "quantity" numeric(14, 3) not null,
  "unit_price" numeric(14, 6) not null,
  "created_at" timestamptz not null default now(),
  unique ("order_id", "line_no"),
  foreign key ("order_id") references business_orders("id") on delete cascade,
  foreign key ("product_id") references products("id") on delete set null
);
create index order_lines_idx_1 on order_lines ("product_id", "order_id");

create table order_step_events (
  "id" uuid not null default gen_random_uuid() primary key,
  "order_id" uuid not null,
  "step_no" smallint not null,
  "completed_by_user_id" uuid,
  "completed_at" timestamptz not null,
  "generated_reference" varchar(80),
  "note" text,
  unique ("order_id", "step_no"),
  foreign key ("order_id") references business_orders("id") on delete cascade,
  foreign key ("completed_by_user_id") references users("id") on delete set null
);
create index order_step_events_idx_1 on order_step_events ("completed_by_user_id", "completed_at");

create table dispatches (
  "id" uuid not null default gen_random_uuid() primary key,
  "order_id" uuid not null unique,
  "dispatch_number" varchar(40) not null unique,
  "dispatched_at" timestamptz not null,
  "reason" dispatch_reason not null,
  "carrier_supplier_id" uuid,
  "vehicle_id" uuid,
  "driver_id" uuid,
  "warehouse_id" uuid not null,
  "destination_snapshot" text not null,
  "created_by_user_id" uuid,
  "created_at" timestamptz not null default now(),
  foreign key ("order_id") references business_orders("id") on delete restrict,
  foreign key ("carrier_supplier_id") references suppliers("id") on delete restrict,
  foreign key ("vehicle_id") references vehicles("id") on delete set null,
  foreign key ("driver_id") references drivers("id") on delete set null,
  foreign key ("warehouse_id") references warehouses("id") on delete restrict,
  foreign key ("created_by_user_id") references users("id") on delete set null
);
create index dispatches_idx_1 on dispatches ("warehouse_id", "dispatched_at");
create index dispatches_idx_2 on dispatches ("carrier_supplier_id");

create table goods_receipts (
  "id" uuid not null default gen_random_uuid() primary key,
  "order_id" uuid not null unique,
  "receipt_number" varchar(40) not null unique,
  "received_at" timestamptz not null,
  "supplier_guide_reference" varchar(80),
  "warehouse_id" uuid not null,
  "condition" goods_condition not null,
  "observations" text,
  "received_by_user_id" uuid,
  "created_at" timestamptz not null default now(),
  foreign key ("order_id") references business_orders("id") on delete restrict,
  foreign key ("warehouse_id") references warehouses("id") on delete restrict,
  foreign key ("received_by_user_id") references users("id") on delete set null
);
create index goods_receipts_idx_1 on goods_receipts ("warehouse_id", "received_at");
create index goods_receipts_idx_2 on goods_receipts ("received_by_user_id");

create table sales_invoices (
  "id" uuid not null default gen_random_uuid() primary key,
  "order_id" uuid unique,
  "customer_id" uuid not null,
  "original_invoice_id" uuid,
  "document_type" sales_document_type not null,
  "series" varchar(12) not null,
  "number" varchar(24) not null,
  "issued_at" date not null,
  "due_at" date,
  "currency" char(3) not null default 'PEN',
  "subtotal" numeric(14, 2) not null,
  "tax_amount" numeric(14, 2) not null,
  "total" numeric(14, 2) not null,
  "tax_status" tax_submission_status not null default 'PENDING',
  "void_reason" text,
  "created_at" timestamptz not null default now(),
  unique ("series", "number"),
  foreign key ("order_id") references business_orders("id") on delete restrict,
  foreign key ("customer_id") references customers("id") on delete restrict,
  foreign key ("original_invoice_id") references sales_invoices("id") on delete restrict
);
create index sales_invoices_idx_1 on sales_invoices ("customer_id", "due_at");
create index sales_invoices_idx_2 on sales_invoices ("tax_status", "issued_at");
create index sales_invoices_idx_3 on sales_invoices ("original_invoice_id");

create table supplier_invoices (
  "id" uuid not null default gen_random_uuid() primary key,
  "order_id" uuid not null unique,
  "supplier_id" uuid not null,
  "series_number" varchar(40) not null,
  "issued_at" date not null,
  "due_at" date not null,
  "currency" char(3) not null default 'PEN',
  "total" numeric(14, 2) not null,
  "validation_status" supplier_invoice_validation_status not null,
  "created_by_user_id" uuid,
  "created_at" timestamptz not null default now(),
  unique ("supplier_id", "series_number"),
  foreign key ("order_id") references business_orders("id") on delete restrict,
  foreign key ("supplier_id") references suppliers("id") on delete restrict,
  foreign key ("created_by_user_id") references users("id") on delete set null
);
create index supplier_invoices_idx_1 on supplier_invoices ("supplier_id", "due_at");
create index supplier_invoices_idx_2 on supplier_invoices ("created_by_user_id");

create table inventory_transfers (
  "id" uuid not null default gen_random_uuid() primary key,
  "code" varchar(24) not null unique,
  "created_at" timestamptz not null default now(),
  "source_warehouse_id" uuid not null,
  "destination_warehouse_id" uuid not null,
  "responsible_user_id" uuid,
  "status" transfer_status not null default 'PENDING',
  "dispatched_at" timestamptz,
  "received_at" timestamptz,
  "updated_at" timestamptz not null default now(),
  foreign key ("source_warehouse_id") references warehouses("id") on delete restrict,
  foreign key ("destination_warehouse_id") references warehouses("id") on delete restrict,
  foreign key ("responsible_user_id") references users("id") on delete set null
);
create index inventory_transfers_idx_1 on inventory_transfers ("status", "created_at");
create index inventory_transfers_idx_2 on inventory_transfers ("source_warehouse_id", "created_at");
create index inventory_transfers_idx_3 on inventory_transfers ("destination_warehouse_id", "created_at");

create table inventory_transfer_lines (
  "id" uuid not null default gen_random_uuid() primary key,
  "transfer_id" uuid not null,
  "line_no" smallint not null,
  "product_id" uuid not null,
  "sku_snapshot" varchar(64) not null,
  "name_snapshot" varchar(200) not null,
  "unit_snapshot" varchar(24) not null,
  "quantity" numeric(14, 3) not null,
  unique ("transfer_id", "line_no"),
  foreign key ("transfer_id") references inventory_transfers("id") on delete cascade,
  foreign key ("product_id") references products("id") on delete restrict
);
create index inventory_transfer_lines_idx_1 on inventory_transfer_lines ("product_id");

create table inventory_adjustments (
  "id" uuid not null default gen_random_uuid() primary key,
  "code" varchar(24) not null unique,
  "product_id" uuid not null,
  "warehouse_id" uuid not null,
  "adjustment_type" adjustment_type not null,
  "reason" text not null,
  "created_by_user_id" uuid,
  "created_at" timestamptz not null default now(),
  foreign key ("product_id") references products("id") on delete restrict,
  foreign key ("warehouse_id") references warehouses("id") on delete restrict,
  foreign key ("created_by_user_id") references users("id") on delete set null
);
create index inventory_adjustments_idx_1 on inventory_adjustments ("product_id", "warehouse_id", "created_at");
create index inventory_adjustments_idx_2 on inventory_adjustments ("created_by_user_id");

create table inventory_movements (
  "id" uuid not null default gen_random_uuid() primary key,
  "occurred_at" timestamptz not null,
  "product_id" uuid not null,
  "warehouse_id" uuid not null,
  "movement_type" inventory_movement_type not null,
  "quantity_delta" numeric(14, 3) not null,
  "unit_cost" numeric(14, 6) not null,
  "order_line_id" uuid,
  "dispatch_id" uuid,
  "goods_receipt_id" uuid,
  "transfer_line_id" uuid,
  "adjustment_id" uuid unique,
  "movement_leg" movement_leg,
  "reference_snapshot" varchar(80) not null,
  "created_by_user_id" uuid,
  "created_at" timestamptz not null default now(),
  unique ("transfer_line_id", "movement_leg"),
  foreign key ("product_id") references products("id") on delete restrict,
  foreign key ("warehouse_id") references warehouses("id") on delete restrict,
  foreign key ("order_line_id") references order_lines("id") on delete restrict,
  foreign key ("dispatch_id") references dispatches("id") on delete restrict,
  foreign key ("goods_receipt_id") references goods_receipts("id") on delete restrict,
  foreign key ("transfer_line_id") references inventory_transfer_lines("id") on delete restrict,
  foreign key ("adjustment_id") references inventory_adjustments("id") on delete restrict,
  foreign key ("created_by_user_id") references users("id") on delete set null
);
create index inventory_movements_idx_1 on inventory_movements ("product_id", "warehouse_id", "occurred_at", "id");
create index inventory_movements_idx_2 on inventory_movements ("warehouse_id", "occurred_at");
create index inventory_movements_idx_3 on inventory_movements ("order_line_id");
create index inventory_movements_idx_4 on inventory_movements ("dispatch_id");
create index inventory_movements_idx_5 on inventory_movements ("goods_receipt_id");
create index inventory_movements_idx_6 on inventory_movements ("transfer_line_id");
create index inventory_movements_idx_7 on inventory_movements ("created_by_user_id");

create table bank_accounts (
  "id" uuid not null default gen_random_uuid() primary key,
  "label" varchar(120) not null unique,
  "bank_name" varchar(80),
  "account_reference" varchar(80),
  "currency" char(3) not null default 'PEN',
  "opening_balance" numeric(14, 2) not null default 0,
  "opening_as_of" date,
  "is_active" boolean not null default true,
  "created_at" timestamptz not null default now(),
  "updated_at" timestamptz not null default now()
);
create index bank_accounts_idx_1 on bank_accounts ("is_active");

create table treasury_movements (
  "id" uuid not null default gen_random_uuid() primary key,
  "code" varchar(32) not null unique,
  "occurred_at" timestamptz not null,
  "channel" treasury_channel not null,
  "movement_type" treasury_movement_type not null,
  "signed_amount" numeric(14, 2) not null,
  "description" text not null,
  "payment_method" payment_method,
  "operation_reference" varchar(80),
  "bank_account_id" uuid,
  "sales_invoice_id" uuid,
  "supplier_invoice_id" uuid,
  "reconciliation_status" reconciliation_status,
  "note" text,
  "created_by_user_id" uuid,
  "created_at" timestamptz not null default now(),
  foreign key ("bank_account_id") references bank_accounts("id") on delete restrict,
  foreign key ("sales_invoice_id") references sales_invoices("id") on delete restrict,
  foreign key ("supplier_invoice_id") references supplier_invoices("id") on delete restrict,
  foreign key ("created_by_user_id") references users("id") on delete set null
);
create index treasury_movements_idx_1 on treasury_movements ("occurred_at", "channel");
create index treasury_movements_idx_2 on treasury_movements ("bank_account_id", "occurred_at");
create index treasury_movements_idx_3 on treasury_movements ("sales_invoice_id", "occurred_at");
create index treasury_movements_idx_4 on treasury_movements ("supplier_invoice_id", "occurred_at");
create index treasury_movements_idx_5 on treasury_movements ("created_by_user_id");

create table company_settings (
  "id" smallint not null default 1 primary key,
  "tax_id" varchar(24) not null,
  "legal_name" varchar(200) not null,
  "fiscal_address" text,
  "currency" char(3) not null default 'PEN',
  "time_zone" varchar(64) not null default 'America/Lima',
  "invoice_series" varchar(12) not null,
  "receipt_series" varchar(12) not null,
  "dispatch_series" varchar(12) not null,
  "credit_note_series" varchar(12) not null,
  "auto_tax_submission" boolean not null default false,
  "email_documents" boolean not null default false,
  "tax_rate" numeric(5, 4) not null default 0.18,
  "prices_include_tax" boolean not null default false,
  "max_discount_pct" numeric(5, 2) not null default 5,
  "quotation_validity_days" smallint not null default 14,
  "cost_method" cost_method not null default 'WEIGHTED_AVERAGE',
  "default_warehouse_id" uuid,
  "allow_negative_stock" boolean not null default false,
  "reorder_alerts_enabled" boolean not null default true,
  "session_duration_hours" smallint not null default 8,
  "max_login_attempts" smallint not null default 5,
  "mfa_required" boolean not null default false,
  "audit_retention_years" smallint not null default 5,
  "updated_at" timestamptz not null default now(),
  foreign key ("default_warehouse_id") references warehouses("id") on delete set null
);
create index company_settings_idx_1 on company_settings ("default_warehouse_id");

create table documents (
  "id" uuid not null default gen_random_uuid() primary key,
  "name" varchar(255) not null,
  "category" document_category not null,
  "storage_key" text unique,
  "mime_type" varchar(120),
  "byte_size" bigint,
  "version_label" varchar(40) not null,
  "related_order_id" uuid,
  "related_customer_id" uuid,
  "related_supplier_id" uuid,
  "uploaded_by_user_id" uuid,
  "created_at" timestamptz not null default now(),
  "deleted_at" timestamptz,
  foreign key ("related_order_id") references business_orders("id") on delete restrict,
  foreign key ("related_customer_id") references customers("id") on delete restrict,
  foreign key ("related_supplier_id") references suppliers("id") on delete restrict,
  foreign key ("uploaded_by_user_id") references users("id") on delete set null
);
create index documents_idx_1 on documents ("category", "created_at");
create index documents_idx_2 on documents ("related_order_id");
create index documents_idx_3 on documents ("related_customer_id");
create index documents_idx_4 on documents ("related_supplier_id");
create index documents_idx_5 on documents ("uploaded_by_user_id");

create table audit_events (
  "id" uuid not null default gen_random_uuid() primary key,
  "occurred_at" timestamptz not null default now(),
  "user_id" uuid,
  "role_id" uuid,
  "user_name_snapshot" varchar(160) not null,
  "role_name_snapshot" varchar(80),
  "action" audit_action not null,
  "module_key" varchar(80) not null,
  "entity_type" varchar(80),
  "entity_id" uuid,
  "reference_snapshot" varchar(120),
  "description" text not null,
  "result" audit_result not null,
  "changes" jsonb not null default '[]'::jsonb,
  "detail" text,
  "ip" inet,
  "device" text,
  "request_id" uuid,
  foreign key ("user_id") references users("id") on delete set null,
  foreign key ("role_id") references roles("id") on delete set null
);
create index audit_events_idx_1 on audit_events ("occurred_at" desc);
create index audit_events_idx_2 on audit_events ("user_id", "occurred_at" desc);
create index audit_events_idx_3 on audit_events ("module_key", "occurred_at" desc);
create index audit_events_idx_4 on audit_events ("entity_type", "entity_id");
create index audit_events_idx_5 on audit_events ("request_id");

create table report_schedules (
  "id" uuid not null default gen_random_uuid() primary key,
  "report_key" varchar(80) not null,
  "recipient_email" varchar(254) not null,
  "frequency" report_frequency not null,
  "is_enabled" boolean not null default true,
  "created_by_user_id" uuid,
  "created_at" timestamptz not null default now(),
  "updated_at" timestamptz not null default now(),
  unique ("report_key"),
  foreign key ("created_by_user_id") references users("id") on delete set null
);
create index report_schedules_idx_1 on report_schedules ("is_enabled", "frequency");
create index report_schedules_idx_2 on report_schedules ("created_by_user_id");

create table document_sequences (
  "document_type" varchar(32) not null,
  "fiscal_year" smallint not null,
  "series" varchar(12) not null default '',
  "last_value" bigint not null default 0,
  "updated_at" timestamptz not null default now(),
  primary key ("document_type", "fiscal_year", "series")
);
create index document_sequences_idx_1 on document_sequences ("document_type", "fiscal_year");

-- updated_at automático (Prisma lo hacía en el cliente)
create or replace function set_updated_at() returns trigger language plpgsql as $$ begin new.updated_at = now(); return new; end $$;
create trigger trg_roles_updated_at before update on roles for each row execute function set_updated_at();
create trigger trg_role_permissions_updated_at before update on role_permissions for each row execute function set_updated_at();
create trigger trg_users_updated_at before update on users for each row execute function set_updated_at();
create trigger trg_customers_updated_at before update on customers for each row execute function set_updated_at();
create trigger trg_suppliers_updated_at before update on suppliers for each row execute function set_updated_at();
create trigger trg_products_updated_at before update on products for each row execute function set_updated_at();
create trigger trg_drivers_updated_at before update on drivers for each row execute function set_updated_at();
create trigger trg_vehicles_updated_at before update on vehicles for each row execute function set_updated_at();
create trigger trg_quotations_updated_at before update on quotations for each row execute function set_updated_at();
create trigger trg_purchase_requests_updated_at before update on purchase_requests for each row execute function set_updated_at();
create trigger trg_business_orders_updated_at before update on business_orders for each row execute function set_updated_at();
create trigger trg_inventory_transfers_updated_at before update on inventory_transfers for each row execute function set_updated_at();
create trigger trg_bank_accounts_updated_at before update on bank_accounts for each row execute function set_updated_at();
create trigger trg_company_settings_updated_at before update on company_settings for each row execute function set_updated_at();
create trigger trg_report_schedules_updated_at before update on report_schedules for each row execute function set_updated_at();
create trigger trg_document_sequences_updated_at before update on document_sequences for each row execute function set_updated_at();

-- Seguridad: el backend usa la service_role key (ignora RLS). Con RLS activo y sin políticas,
-- la anon key pública de Supabase no puede leer ni escribir ninguna tabla.
alter table roles enable row level security;
alter table permissions enable row level security;
alter table role_permissions enable row level security;
alter table users enable row level security;
alter table auth_sessions enable row level security;
alter table customers enable row level security;
alter table suppliers enable row level security;
alter table product_categories enable row level security;
alter table products enable row level security;
alter table warehouses enable row level security;
alter table drivers enable row level security;
alter table vehicles enable row level security;
alter table quotations enable row level security;
alter table quotation_lines enable row level security;
alter table purchase_requests enable row level security;
alter table purchase_request_lines enable row level security;
alter table business_orders enable row level security;
alter table order_lines enable row level security;
alter table order_step_events enable row level security;
alter table dispatches enable row level security;
alter table goods_receipts enable row level security;
alter table sales_invoices enable row level security;
alter table supplier_invoices enable row level security;
alter table inventory_transfers enable row level security;
alter table inventory_transfer_lines enable row level security;
alter table inventory_adjustments enable row level security;
alter table inventory_movements enable row level security;
alter table bank_accounts enable row level security;
alter table treasury_movements enable row level security;
alter table company_settings enable row level security;
alter table documents enable row level security;
alter table audit_events enable row level security;
alter table report_schedules enable row level security;
alter table document_sequences enable row level security;
