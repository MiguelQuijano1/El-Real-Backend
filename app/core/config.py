import re
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DURATION = re.compile(r"^([1-9]\d*)([smhd])$")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def duration_to_seconds(value: str) -> int:
    match = _DURATION.match(value)
    if not match:
        raise ValueError("debe ser una duración positiva como 30m, 8h o 7d")
    return int(match.group(1)) * _UNITS[match.group(2)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["development", "production", "test"] = "development"
    cors_origins: str = "http://localhost:5173"
    trust_proxy: bool = False
    # Límite global de peticiones por minuto y por IP (0 = sin límite). Con la actualización automática (WebSocket)
    # cada cambio hace que los demás clientes relean datos, así que conviene dejar margen si varios comparten red.
    global_rate_limit_per_minute: int = Field(default=600, ge=0)

    supabase_url: str
    supabase_service_role_key: str
    supabase_storage_bucket: str = "documents"

    jwt_secret: str
    jwt_expires_in: str = "8h"

    max_upload_mb: int = 20

    seed_admin_email: str | None = None
    seed_admin_name: str = "Administrador del sistema"
    seed_admin_password: str | None = None

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_strong(cls, v: str) -> str:
        if len(v.strip()) < 32:
            raise ValueError("JWT_SECRET es obligatorio y debe tener al menos 32 caracteres")
        return v

    @field_validator("jwt_expires_in")
    @classmethod
    def _jwt_duration(cls, v: str) -> str:
        duration_to_seconds(v)
        return v

    @field_validator("supabase_url")
    @classmethod
    def _supabase_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("SUPABASE_URL debe empezar con https://")
        return v.rstrip("/")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def jwt_expires_seconds(self) -> int:
        return duration_to_seconds(self.jwt_expires_in)

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
