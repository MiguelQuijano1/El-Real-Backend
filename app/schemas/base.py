from pydantic import BaseModel, ConfigDict

from app.core.serialization import to_camel


class ApiModel(BaseModel):
    """Entrada en camelCase (como el frontend); internamente snake_case = columnas de la BD."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid", str_strip_whitespace=True)
