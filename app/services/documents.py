import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import UploadFile

from app.core.config import get_settings
from app.core.errors import ApiError, bad_request, not_found
from app.repositories.client import db, execute, first, rows
from app.security.deps import AuthUser
from app.services.audit import RequestMeta, record

ALLOWED_MIME = {
    "application/pdf", "image/png", "image/jpeg", "image/webp", "text/plain", "text/csv",
    "application/zip", "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
DOC_SELECT = ("id, name, category, mime_type, byte_size, version_label, related_order_id, related_customer_id, "
              "related_supplier_id, uploaded_by_user_id, created_at")  # storage_key nunca sale al cliente


_UNSAFE = re.compile(r"[%*,()\\]")


def _storage():
    return db().storage.from_(get_settings().supabase_storage_bucket)


def list_documents(*, category: str | None, order_id: str | None, customer_id: str | None, supplier_id: str | None,
                   q: str | None, limit: int, offset: int) -> dict[str, Any]:
    query = db().table("documents").select(DOC_SELECT, count="exact").is_("deleted_at", "null")
    if category:
        query = query.eq("category", category)
    if order_id:
        query = query.eq("related_order_id", order_id)
    if customer_id:
        query = query.eq("related_customer_id", customer_id)
    if supplier_id:
        query = query.eq("related_supplier_id", supplier_id)
    if q:
        term = _UNSAFE.sub(" ", q).strip()
        if term:
            query = query.ilike("name", f"%{term}%")
    res = execute(query.order("created_at", desc=True).range(offset, offset + limit - 1))
    return {"items": res.data or [], "total": res.count or 0, "limit": limit, "offset": offset}


def _get(document_id: str) -> dict[str, Any]:
    doc = first(db().table("documents").select(DOC_SELECT + ", storage_key").eq("id", document_id).is_("deleted_at", "null"))
    if not doc:
        raise not_found("Document not found")
    return doc


def upload_document(file: UploadFile, *, category: str, version_label: str, related: dict[str, str | None],
                    actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    content = file.file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ApiError(413, f"File too large (max {settings.max_upload_mb} MB)")
    if not content:
        raise bad_request("Empty file")
    mime = (file.content_type or "application/octet-stream").split(";")[0].strip().lower()
    if mime not in ALLOWED_MIME:
        raise ApiError(415, "File type not allowed")

    original = (file.filename or "file").strip()
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", original)[-120:] or "file"
    now = datetime.now(timezone.utc)
    key = f"{now:%Y/%m}/{uuid.uuid4()}-{safe_name}"

    _storage().upload(key, content, {"content-type": mime, "upsert": "false"})
    try:
        row = execute(db().table("documents").insert({
            "name": original[:255], "category": category, "storage_key": key, "mime_type": mime,
            "byte_size": len(content), "version_label": version_label,
            "related_order_id": related.get("order_id"), "related_customer_id": related.get("customer_id"),
            "related_supplier_id": related.get("supplier_id"), "uploaded_by_user_id": actor.id,
        })).data[0]
    except Exception:
        _storage().remove([key])  # no dejar archivos huérfanos si falla el registro
        raise
    record(actor.actor, action="CREATE", module_key="documents", entity_type="Document", entity_id=row["id"],
           reference=original[:120], description=f"Documento subido · {original}", meta=meta)
    row.pop("storage_key", None)
    return row


def signed_download_url(document_id: str, actor: AuthUser, meta: RequestMeta, expires_in: int = 60) -> dict[str, Any]:
    doc = _get(document_id)
    res = _storage().create_signed_url(doc["storage_key"], expires_in, {"download": doc["name"]})
    url = res.get("signedURL") or res.get("signedUrl") or res.get("signed_url")
    if not url:
        raise ApiError(502, "Storage did not return a download URL")
    record(actor.actor, action="DOWNLOAD", module_key="documents", entity_type="Document", entity_id=doc["id"],
           reference=doc["name"][:120], description=f"Descarga de documento · {doc['name']}", meta=meta)
    return {"url": url, "expires_in": expires_in}


def delete_document(document_id: str, actor: AuthUser, meta: RequestMeta) -> None:
    """Baja lógica: el archivo se conserva en Storage según la política de conservación."""
    doc = _get(document_id)
    execute(db().table("documents").update({"deleted_at": datetime.now(timezone.utc).isoformat()}).eq("id", document_id))
    record(actor.actor, action="DELETE", module_key="documents", entity_type="Document", entity_id=doc["id"],
           reference=doc["name"][:120], description=f"Documento eliminado · {doc['name']}", meta=meta)
