from typing import Literal
from uuid import UUID

from pydantic import EmailStr, Field

from app.schemas.base import ApiModel


class CreateUserIn(ApiModel):
    full_name: str = Field(min_length=1, max_length=160)
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128)
    role_id: UUID
    area: str | None = Field(default=None, max_length=60)


class UpdateUserIn(ApiModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=160)
    email: EmailStr | None = Field(default=None, max_length=254)
    area: str | None = Field(default=None, max_length=60)


class SetStatusIn(ApiModel):
    status: Literal["ACTIVE", "INACTIVE", "LOCKED"]


class AssignRoleIn(ApiModel):
    role_id: UUID


class ResetPasswordIn(ApiModel):
    new_password: str = Field(min_length=8, max_length=128)
