from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

from app.core.errors import bad_request, not_found
from app.repositories.client import db, execute, first, rows
from app.security.deps import AuthUser
from app.services.audit import RequestMeta, record

MODULE_AR = "ar"
MODULE_AP = "ap"
MODULE_CASH = "cash"
MODULE_BANKS = "banks"

RECON_UI = {"PENDING": "Pendiente", "RECONCILED": "Conciliado"}
UI_RECON = {v: k for k, v in RECON_UI.items()}

TYPE_UI = {
    "COLLECTION": "Cobro",
    "PAYMENT": "Pago",
    "OPENING": "Apertura",
    "BANK_FEE": "Comisión",
    "MANUAL_INCOME": "Ingreso",
    "MANUAL_EXPENSE": "Egreso",
}


def _next_code(prefix: str) -> str:
    y = datetime.now(timezone.utc).year
    n = execute(db().rpc("next_sequence", {"p_type": prefix, "p_year": y, "p_series": ""})).data
    n = n[0] if isinstance(n, list) else n
    return f"{prefix}-{y}-{int(n):04d}"


def _due_status(due: date | None, saldo: float) -> tuple[str, str]:
    if saldo <= 0.01:
        return "Cobrada" if True else "Pagada", "cancelada"
    if not due:
        return "Por vencer", "sin fecha"
    today = date.today()
    delta = (due - today).days
    if delta < 0:
        return "Vencida", f"{abs(delta)} días de atraso"
    if delta <= 7:
        return "Vence esta semana", f"en {delta} días"
    return "Por vencer", f"en {delta} días"


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except Exception:
        return None


# --------------------------------------------------------------------------- CxC
def list_receivables(*, q: str | None = None, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """CxC = facturas de venta no anuladas, saldo = total − cobros en tesorería."""
    invoices = rows(
        db().table("sales_invoices").select(
            "*, customer:customers(id, legal_name, document_number), order:business_orders(code)"
        ).neq("tax_status", "VOIDED").neq("document_type", "CREDIT_NOTE")
    )
    collections: dict[str, float] = defaultdict(float)
    for m in rows(
        db().table("treasury_movements")
        .select("sales_invoice_id, signed_amount")
        .eq("movement_type", "COLLECTION")
    ):
        sid = m.get("sales_invoice_id")
        if sid:
            collections[sid] += abs(float(m.get("signed_amount") or 0))

    items = []
    for inv in invoices:
        total = float(inv.get("total") or 0)
        paid = collections.get(inv["id"], 0)
        saldo = max(0, round(total - paid, 2))
        cust = inv.get("customer") or {}
        order = inv.get("order") or {}
        due = _parse_date(str(inv.get("due_at") or ""))
        estado, dias = _due_status(due, saldo)
        if saldo <= 0.01:
            estado, dias = "Cobrada", "cancelada"
        num = f"{inv.get('series')}-{inv.get('number')}"
        row = {
            "id": inv["id"],
            "doc": num,
            "emision": str(inv.get("issued_at") or ""),
            "cliente": cust.get("legal_name") or "",
            "ruc": cust.get("document_number") or "",
            "vence": str(inv.get("due_at") or "—"),
            "total": total,
            "saldo": saldo,
            "estado": estado,
            "dias": dias,
            "orden": order.get("code") or "",
        }
        if q:
            term = q.strip().lower()
            if term not in f"{row['doc']} {row['cliente']} {row['ruc']}".lower():
                continue
        items.append(row)

    items.sort(key=lambda r: (0 if r["estado"] == "Vencida" else 1 if r["estado"] == "Vence esta semana" else 2, r["vence"]))
    total_n = len(items)
    return {"items": items[offset : offset + limit], "total": total_n, "limit": limit, "offset": offset}


def collect_receivable(
    invoice_id: str,
    payload: dict[str, Any],
    actor: AuthUser,
    meta: RequestMeta,
) -> dict[str, Any]:
    inv = first(db().table("sales_invoices").select("*").eq("id", invoice_id))
    if not inv:
        # try series-number
        if "-" in invoice_id:
            series, number = invoice_id.split("-", 1)
            inv = first(db().table("sales_invoices").select("*").eq("series", series).eq("number", number))
    if not inv:
        raise not_found("Comprobante no encontrado")

    amount = abs(float(payload.get("amount") or 0))
    if amount <= 0:
        raise bad_request("El monto debe ser mayor a 0")

    # saldo actual
    paid = sum(
        abs(float(m.get("signed_amount") or 0))
        for m in rows(
            db().table("treasury_movements")
            .select("signed_amount")
            .eq("sales_invoice_id", inv["id"])
            .eq("movement_type", "COLLECTION")
        )
    )
    saldo = max(0, float(inv.get("total") or 0) - paid)
    if amount > saldo + 0.01:
        raise bad_request(f"El monto supera el saldo pendiente ({saldo})")

    channel = (payload.get("channel") or "CASH").upper()
    if channel not in ("CASH", "BANK"):
        channel = "CASH" if "caja" in str(payload.get("channel") or "").lower() or "efectivo" in str(payload.get("channel") or "").lower() else "BANK"
        if channel not in ("CASH", "BANK"):
            channel = "CASH"

    bank_account_id = payload.get("bank_account_id")
    if channel == "BANK" and not bank_account_id:
        acc = first(db().table("bank_accounts").select("id").eq("is_active", True))
        bank_account_id = (acc or {}).get("id")

    code = _next_code("COB")
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "code": code,
        "occurred_at": payload.get("occurred_at") or now,
        "channel": channel,
        "movement_type": "COLLECTION",
        "signed_amount": amount,  # cobro positivo
        "description": payload.get("description") or f"Cobro {inv.get('series')}-{inv.get('number')}",
        "payment_method": payload.get("payment_method") or ("CASH" if channel == "CASH" else "TRANSFER"),
        "operation_reference": payload.get("reference"),
        "bank_account_id": bank_account_id if channel == "BANK" else None,
        "sales_invoice_id": inv["id"],
        "reconciliation_status": "RECONCILED" if channel == "CASH" else "PENDING",
        "created_by_user_id": actor.id,
    }
    execute(db().table("treasury_movements").insert(row))
    record(
        actor.actor, action="CREATE", module_key=MODULE_AR, entity_type="TreasuryMovement",
        entity_id=None, reference=code,
        description=f"Cobro {amount} · {inv.get('series')}-{inv.get('number')}", meta=meta,
    )
    items = list_receivables(limit=200, offset=0)["items"]
    for i in items:
        if i["id"] == inv["id"]:
            return i
    return {"ok": True, "doc": f"{inv.get('series')}-{inv.get('number')}"}


# --------------------------------------------------------------------------- CxP
def list_payables(*, q: str | None = None, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    invoices = rows(
        db().table("supplier_invoices").select(
            "*, supplier:suppliers(id, legal_name, tax_id), order:business_orders(code)"
        )
    )
    payments: dict[str, float] = defaultdict(float)
    for m in rows(
        db().table("treasury_movements")
        .select("supplier_invoice_id, signed_amount")
        .eq("movement_type", "PAYMENT")
    ):
        sid = m.get("supplier_invoice_id")
        if sid:
            payments[sid] += abs(float(m.get("signed_amount") or 0))

    items = []
    for inv in invoices:
        total = float(inv.get("total") or 0)
        paid = payments.get(inv["id"], 0)
        saldo = max(0, round(total - paid, 2))
        sup = inv.get("supplier") or {}
        order = inv.get("order") or {}
        due = _parse_date(str(inv.get("due_at") or ""))
        estado, dias = _due_status(due, saldo)
        if saldo <= 0.01:
            estado, dias = "Pagada", "cancelada"
        # rename Por vencer labels for payables are same
        doc = inv.get("series_number") or inv.get("code") or inv["id"][:8]
        row = {
            "id": inv["id"],
            "doc": doc,
            "emision": str(inv.get("issued_at") or ""),
            "prov": sup.get("legal_name") or "",
            "ruc": sup.get("tax_id") or "",
            "vence": str(inv.get("due_at") or "—"),
            "total": total,
            "saldo": saldo,
            "estado": estado if estado != "Cobrada" else "Pagada",
            "dias": dias,
            "orden": order.get("code") or "",
        }
        if q:
            term = q.strip().lower()
            if term not in f"{row['doc']} {row['prov']} {row['ruc']}".lower():
                continue
        items.append(row)

    items.sort(key=lambda r: (0 if r["estado"] == "Vencida" else 1 if r["estado"] == "Vence esta semana" else 2, r["vence"]))
    total_n = len(items)
    return {"items": items[offset : offset + limit], "total": total_n, "limit": limit, "offset": offset}


def pay_payable(invoice_id: str, payload: dict[str, Any], actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    inv = first(db().table("supplier_invoices").select("*").eq("id", invoice_id))
    if not inv:
        inv = first(db().table("supplier_invoices").select("*").eq("series_number", invoice_id))
    if not inv:
        raise not_found("Factura de proveedor no encontrada")

    amount = abs(float(payload.get("amount") or 0))
    if amount <= 0:
        raise bad_request("El monto debe ser mayor a 0")

    paid = sum(
        abs(float(m.get("signed_amount") or 0))
        for m in rows(
            db().table("treasury_movements")
            .select("signed_amount")
            .eq("supplier_invoice_id", inv["id"])
            .eq("movement_type", "PAYMENT")
        )
    )
    saldo = max(0, float(inv.get("total") or 0) - paid)
    if amount > saldo + 0.01:
        raise bad_request(f"El monto supera el saldo pendiente ({saldo})")

    channel = (payload.get("channel") or "BANK").upper()
    if channel not in ("CASH", "BANK"):
        channel = "BANK"

    if channel == "CASH":
        available = cash_balance()
        if amount > available + 0.01:
            raise bad_request(f"Saldo de caja insuficiente para pagar {amount:.2f} (disponible {available:.2f})")

    bank_account_id = payload.get("bank_account_id")
    if channel == "BANK" and not bank_account_id:
        acc = first(db().table("bank_accounts").select("id").eq("is_active", True))
        bank_account_id = (acc or {}).get("id")

    code = _next_code("PAG")
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "code": code,
        "occurred_at": payload.get("occurred_at") or now,
        "channel": channel,
        "movement_type": "PAYMENT",
        "signed_amount": -amount,  # pago negativo
        "description": payload.get("description") or f"Pago {inv.get('series_number')}",
        "payment_method": payload.get("payment_method") or ("CASH" if channel == "CASH" else "TRANSFER"),
        "operation_reference": payload.get("reference"),
        "bank_account_id": bank_account_id if channel == "BANK" else None,
        "supplier_invoice_id": inv["id"],
        "reconciliation_status": "RECONCILED" if channel == "CASH" else "PENDING",
        "created_by_user_id": actor.id,
    }
    execute(db().table("treasury_movements").insert(row))
    record(
        actor.actor, action="CREATE", module_key=MODULE_AP, entity_type="TreasuryMovement",
        entity_id=None, reference=code,
        description=f"Pago {amount} · {inv.get('series_number')}", meta=meta,
    )
    items = list_payables(limit=200, offset=0)["items"]
    for i in items:
        if i["id"] == inv["id"]:
            return i
    return {"ok": True}


# --------------------------------------------------------------------------- Caja
def list_cash(*, q: str | None = None, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    query = (
        db().table("treasury_movements")
        .select("*", count="exact")
        .eq("channel", "CASH")
    )
    res = execute(query.order("occurred_at", desc=True).range(offset, offset + limit - 1))
    items = []
    for m in res.data or []:
        amt = float(m.get("signed_amount") or 0)
        row = {
            "id": m["id"],
            "code": m.get("code"),
            "fecha": str(m.get("occurred_at") or "")[:19].replace("T", " "),
            "tipo": TYPE_UI.get(m.get("movement_type") or "", m.get("movement_type") or ""),
            "desc": m.get("description") or "",
            "ref": m.get("operation_reference") or "",
            "monto": amt,
            "estado": RECON_UI.get(m.get("reconciliation_status") or "PENDING", m.get("reconciliation_status") or "Pendiente"),
        }
        if q:
            term = q.strip().lower()
            if term not in f"{row['desc']} {row['ref']} {row['code']}".lower():
                continue
        items.append(row)
    return {"items": items, "total": res.count or len(items), "limit": limit, "offset": offset}


def cash_balance() -> float:
    total = 0.0
    for m in rows(db().table("treasury_movements").select("signed_amount").eq("channel", "CASH")):
        total += float(m.get("signed_amount") or 0)
    return round(total, 2)


def create_cash_movement(payload: dict[str, Any], actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    amount = abs(float(payload.get("amount") or 0))
    if amount <= 0:
        raise bad_request("El monto debe ser mayor a 0")
    kind = (payload.get("movement_type") or "MANUAL_INCOME").upper()
    # Abono/Ingreso positivo, Cargo/Egreso negativo
    tip_ui = str(payload.get("tipo") or "").lower()
    if tip_ui in ("cargo", "egreso", "salida"):
        kind = "MANUAL_EXPENSE"
        signed = -amount
    elif tip_ui in ("abono", "ingreso", "entrada"):
        kind = "MANUAL_INCOME"
        signed = amount
    elif kind in ("MANUAL_EXPENSE", "PAYMENT", "BANK_FEE"):
        signed = -amount
    else:
        signed = amount
        kind = kind if kind in TYPE_UI else "MANUAL_INCOME"

    code = _next_code("CAJ")
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "code": code,
        "occurred_at": payload.get("occurred_at") or now,
        "channel": "CASH",
        "movement_type": kind,
        "signed_amount": signed,
        "description": payload.get("description") or "Movimiento de caja",
        "payment_method": payload.get("payment_method") or "CASH",
        "operation_reference": payload.get("reference"),
        "reconciliation_status": "RECONCILED",
        "created_by_user_id": actor.id,
    }
    created = execute(db().table("treasury_movements").insert(row)).data[0]
    record(
        actor.actor, action="CREATE", module_key=MODULE_CASH, entity_type="TreasuryMovement",
        entity_id=created["id"], reference=code, description=row["description"], meta=meta,
    )
    return list_cash(limit=1, offset=0)["items"][0] if list_cash(limit=1)["items"] else {"code": code}


# --------------------------------------------------------------------------- Bancos
def list_bank_accounts() -> list[dict[str, Any]]:
    accounts = rows(db().table("bank_accounts").select("*").eq("is_active", True).order("label"))
    balances: dict[str, float] = {}
    for m in rows(
        db().table("treasury_movements")
        .select("bank_account_id, signed_amount")
        .eq("channel", "BANK")
    ):
        bid = m.get("bank_account_id")
        if bid:
            balances[bid] = balances.get(bid, 0) + float(m.get("signed_amount") or 0)

    return [
        {
            "id": a["id"],
            "label": a.get("label"),
            "bankName": a.get("bank_name"),
            "accountReference": a.get("account_reference"),
            "currency": a.get("currency") or "PEN",
            "openingBalance": float(a.get("opening_balance") or 0),
            "balance": round(float(a.get("opening_balance") or 0) + balances.get(a["id"], 0), 2),
        }
        for a in accounts
    ]


def list_bank_movements(*, q: str | None = None, bank_account_id: str | None = None, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    query = db().table("treasury_movements").select(
        "*, bank:bank_accounts(id, label, bank_name)",
        count="exact",
    ).eq("channel", "BANK")
    if bank_account_id:
        query = query.eq("bank_account_id", bank_account_id)
    res = execute(query.order("occurred_at", desc=True).range(offset, offset + limit - 1))
    items = []
    for m in res.data or []:
        bank = m.get("bank") or {}
        amt = float(m.get("signed_amount") or 0)
        row = {
            "id": m["id"],
            "code": m.get("code"),
            "fecha": str(m.get("occurred_at") or "")[:10],
            "desc": m.get("description") or "",
            "cuenta": bank.get("label") or "",
            "ref": m.get("operation_reference") or "",
            "monto": amt,
            "estado": RECON_UI.get(m.get("reconciliation_status") or "PENDING", "Pendiente"),
            "bankAccountId": m.get("bank_account_id"),
        }
        if q:
            term = q.strip().lower()
            if term not in f"{row['desc']} {row['cuenta']} {row['ref']}".lower():
                continue
        items.append(row)
    return {"items": items, "total": res.count or len(items), "limit": limit, "offset": offset}


def create_bank_movement(payload: dict[str, Any], actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    amount = abs(float(payload.get("amount") or 0))
    if amount <= 0:
        raise bad_request("El monto debe ser mayor a 0")
    bank_account_id = payload.get("bank_account_id")
    if not bank_account_id and payload.get("cuenta"):
        acc = first(db().table("bank_accounts").select("id").eq("label", payload["cuenta"]))
        bank_account_id = (acc or {}).get("id")
    if not bank_account_id:
        raise bad_request("Selecciona una cuenta bancaria")

    tip = str(payload.get("tipo") or "").lower()
    if tip in ("cargo", "egreso"):
        signed = -amount
        kind = "MANUAL_EXPENSE"
    else:
        signed = amount
        kind = "MANUAL_INCOME"

    code = _next_code("BAN")
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "code": code,
        "occurred_at": payload.get("occurred_at") or now,
        "channel": "BANK",
        "movement_type": kind,
        "signed_amount": signed,
        "description": payload.get("description") or "Movimiento bancario",
        "payment_method": payload.get("payment_method") or "TRANSFER",
        "operation_reference": payload.get("reference"),
        "bank_account_id": bank_account_id,
        "reconciliation_status": "PENDING",
        "created_by_user_id": actor.id,
    }
    created = execute(db().table("treasury_movements").insert(row)).data[0]
    record(
        actor.actor, action="CREATE", module_key=MODULE_BANKS, entity_type="TreasuryMovement",
        entity_id=created["id"], reference=code, description=row["description"], meta=meta,
    )
    items = list_bank_movements(limit=5, offset=0)["items"]
    return items[0] if items else {"code": code}


def reconcile_movement(movement_id: str, actor: AuthUser, meta: RequestMeta, reference: str | None = None) -> dict[str, Any]:
    m = first(db().table("treasury_movements").select("*").eq("id", movement_id))
    if not m:
        raise not_found("Movimiento no encontrado")
    patch: dict[str, Any] = {"reconciliation_status": "RECONCILED"}
    if reference:
        patch["operation_reference"] = reference
    execute(db().table("treasury_movements").update(patch).eq("id", movement_id))
    record(
        actor.actor, action="EDIT", module_key=MODULE_BANKS, entity_type="TreasuryMovement",
        entity_id=movement_id, reference=m.get("code"), description="Movimiento conciliado", meta=meta,
    )
    return {"ok": True, "id": movement_id}