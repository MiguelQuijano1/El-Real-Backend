from pydantic import EmailStr, Field

from app.schemas.base import ApiModel


class LoginIn(ApiModel):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128)


class ChangePasswordIn(ApiModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
