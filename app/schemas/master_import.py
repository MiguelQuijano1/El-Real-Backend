from typing import Any

from pydantic import Field

from app.schemas.base import ApiModel


class ImportIn(ApiModel):
    """Lote de filas ya convertidas por el frontend al formato del alta (camelCase), como en POST del maestro."""
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=2000)
    # True: solo valida y dice qué pasaría. False: guarda (todo o nada).
    dry_run: bool = True
    # Campos (camelCase) que trae el archivo. Al ACTUALIZAR un registro existente solo se cambian estos;
    # los demás conservan su valor. Sin la lista, se actualizan todos los campos del modelo.
    fields: list[str] | None = Field(default=None, max_length=60)