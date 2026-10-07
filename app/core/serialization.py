from typing import Any


def to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in tail)


def camel(value: Any) -> Any:
    """snake_case de la BD → camelCase del API (solo claves de diccionario)."""
    if isinstance(value, dict):
        return {to_camel(k) if isinstance(k, str) else k: camel(v) for k, v in value.items()}
    if isinstance(value, list):
        return [camel(v) for v in value]
    return value
