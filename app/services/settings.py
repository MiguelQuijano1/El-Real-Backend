from typing import Any

from app.core.errors import bad_request, not_found
from app.repositories.client import db, execute, first
from app.schemas.settings import UpdateSettingsIn
from app.security.deps import AuthUser
from app.services.audit import RequestMeta, record


def get_settings_row() -> dict[str, Any]:
    row = first(db().table("company_settings").select("*").eq("id", 1))
    if not row:
        raise not_found("Company settings not initialised. Run: python -m scripts.seed")
    return row


def update_settings(dto: UpdateSettingsIn, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    patch = dto.model_dump(exclude_unset=True, mode="json")
    if not patch:
        raise bad_request("At least one field must be provided")
    before = get_settings_row()
    execute(db().table("company_settings").update(patch).eq("id", 1))
    after = get_settings_row()
    changes = [(k, str(before[k]), str(after[k])) for k in patch if str(before[k]) != str(after[k])]
    if changes:
        record(actor.actor, action="EDIT", module_key="settings", entity_type="CompanySetting",
               description="Configuración actualizada", changes=changes, meta=meta)
    return after
