import re

from pydantic import Field, field_validator

from app.schemas.base import ApiModel
from app.security.catalog import ALL_MODULE_KEYS, PERMISSION_ACTIONS


class CreateRoleIn(ApiModel):
    code: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=500)

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        v = v.upper()
        if not re.fullmatch(r"[A-Z0-9_]+", v):
            raise ValueError("solo letras, números y guion bajo")
        return v


class UpdateRoleIn(ApiModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


class SetRolePermissionsIn(ApiModel):
    # { "customers": ["view", "create"], "products": ["view"] } — lo que no aparezca queda sin permiso.
    permissions: dict[str, list[str]]

    @field_validator("permissions")
    @classmethod
    def _valid(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        for module, actions in v.items():
            if module not in ALL_MODULE_KEYS:
                raise ValueError(f"módulo desconocido: {module}")
            bad = [a for a in actions if a not in PERMISSION_ACTIONS]
            if bad:
                raise ValueError(f"acción desconocida en {module}: {bad[0]}")
        return v
