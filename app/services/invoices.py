from __future__ import annotations

from typing import Any

from app.core.errors import bad_request, not_found
from app.repositories.client import db, execute, first
from app.security.deps import AuthUser
from app.services.audit import RequestMeta, record

MODULE = "receipts"

TYPE_UI = {"INVOICE": "Factura", "RECEIPT": "Boleta", "CREDIT_NOTE": "Nota de crédito"}
STATUS_UI = {"PENDING": "Pendiente", "ACCEPTED": "Aceptado", "REJECTED": "Rechazado", "VOIDED": "Anulado"}
UI_STATUS = {v: k for k, v in STATUS_UI.items()}


def _embed() -> str:
    return (
        "*, "
        "customer:customers(id, code, legal_name, document_number), "
        "order:business_orders(id, code)"
    )


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    customer = row.get("customer") or {}
    order = row.get("order") or {}
    doc_type = row.get("document_type") or "INVOICE"
    tax = row.get("tax_status") or "PENDING"
    num = f"{row.get('series')}-{row.get('number')}"
    return {
        "id": row["id"],
        "num": num,
        "series": row.get("series"),
        "number": row.get("number"),
        "tipo": TYPE_UI.get(doc_type, doc_type),
        "documentType": doc_type,
        "cliente": customer.get("legal_name") or "",
        "ruc": customer.get("document_number") or "",
        "customerId": row.get("customer_id"),
        "emision": str(row.get("issued_at") or ""),
        "vence": str(row.get("due_at") or "—"),
        "total": float(row.get("total") or 0),
        "subtotal": float(row.get("subtotal") or 0),
        "taxAmount": float(row.get("tax_amount") or 0),
        "sunat": STATUS_UI.get(tax, tax),
        "taxStatus": tax,
        "orden": order.get("code") or "",
        "orderId": row.get("order_id"),
        "voidReason": row.get("void_reason"),
        "createdAt": row.get("created_at"),
        "currency": row.get("currency") or "PEN",
    }


def list_invoices(
    *,
    q: str | None = None,
    tax_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    query = db().table("sales_invoices").select(_embed(), count="exact")
    if tax_status:
        key = UI_STATUS.get(tax_status, tax_status)
        if key in STATUS_UI:
            query = query.eq("tax_status", key)
    if q:
        term = q.strip().replace(",", " ")
        if term:
            query = query.or_(f"series.ilike.%{term}%,number.ilike.%{term}%")
    res = execute(query.order("issued_at", desc=True).range(offset, offset + limit - 1))
    items = [_serialize(r) for r in (res.data or [])]
    return {"items": items, "total": res.count or 0, "limit": limit, "offset": offset}


def get_invoice(invoice_id: str) -> dict[str, Any]:
    row = first(db().table("sales_invoices").select(_embed()).eq("id", invoice_id))
    if not row:
        # buscar por serie-numero
        if "-" in invoice_id:
            series, number = invoice_id.split("-", 1)
            row = first(
                db().table("sales_invoices").select(_embed()).eq("series", series).eq("number", number)
            )
    if not row:
        raise not_found("Comprobante no encontrado")
    return _serialize(row)


def set_tax_status(invoice_id: str, tax_status: str, actor: AuthUser, meta: RequestMeta, void_reason: str | None = None) -> dict[str, Any]:
    key = UI_STATUS.get(tax_status, tax_status)
    if key not in STATUS_UI:
        raise bad_request("Estado SUNAT inválido")
    current = first(db().table("sales_invoices").select("*").eq("id", invoice_id))
    if not current:
        # try by series-number
        inv = get_invoice(invoice_id)
        current = first(db().table("sales_invoices").select("*").eq("id", inv["id"]))
    if not current:
        raise not_found("Comprobante no encontrado")
    patch: dict[str, Any] = {"tax_status": key}
    if key == "VOIDED" and void_reason:
        patch["void_reason"] = void_reason
    execute(db().table("sales_invoices").update(patch).eq("id", current["id"]))
    record(
        actor.actor,
        action="EDIT",
        module_key=MODULE,
        entity_type="SalesInvoice",
        entity_id=current["id"],
        reference=f"{current.get('series')}-{current.get('number')}",
        description=f"Comprobante · estado {STATUS_UI[key]}",
        meta=meta,
        changes=[("tax_status", str(current.get("tax_status")), key)],
    )
    return get_invoice(current["id"])


def void_with_credit_note(invoice_id: str, reason: str, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    inv = get_invoice(invoice_id)
    if inv["documentType"] == "CREDIT_NOTE":
        raise bad_request("Una nota de crédito no se anula con otra nota de crédito")
    if inv["taxStatus"] == "VOIDED":
        raise bad_request("El comprobante ya está anulado")

    settings = first(db().table("company_settings").select("credit_note_series").eq("id", 1)) or {}
    series = settings.get("credit_note_series") or "FC01"
    from datetime import datetime, timezone
    y = datetime.now(timezone.utc).year
    n = execute(db().rpc("next_sequence", {"p_type": "NC", "p_year": y, "p_series": series})).data
    n = n[0] if isinstance(n, list) else n
    number = f"{int(n):05d}"
    today = datetime.now(timezone.utc).date().isoformat()
    total = -abs(float(inv["total"]))
    subtotal = -abs(float(inv["subtotal"]))
    tax = -abs(float(inv["taxAmount"]))
    row = {
        "order_id": None,
        "customer_id": inv["customerId"],
        "original_invoice_id": inv["id"],
        "document_type": "CREDIT_NOTE",
        "series": series,
        "number": number,
        "issued_at": today,
        "due_at": today,
        "currency": inv.get("currency") or "PEN",
        "subtotal": subtotal,
        "tax_amount": tax,
        "total": total,
        "tax_status": "ACCEPTED",
        "void_reason": reason,
    }
    created = execute(db().table("sales_invoices").insert(row)).data[0]
    execute(
        db()
        .table("sales_invoices")
        .update({"tax_status": "VOIDED", "void_reason": reason})
        .eq("id", inv["id"])
    )
    record(
        actor.actor,
        action="CREATE",
        module_key=MODULE,
        entity_type="SalesInvoice",
        entity_id=created["id"],
        reference=f"{series}-{number}",
        description=f"Nota de crédito por {inv['num']} · {reason}",
        meta=meta,
    )
    return {"original": get_invoice(inv["id"]), "creditNote": get_invoice(created["id"])}