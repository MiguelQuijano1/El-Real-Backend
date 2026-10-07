import uuid

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# argon2id con los parámetros por defecto recomendados de argon2-cffi.
_hasher = PasswordHasher()
_dummy_hash: str | None = None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hash_: str, password: str) -> bool:
    try:
        return _hasher.verify(hash_, password)
    except (VerificationError, InvalidHashError):
        return False


def dummy_hash() -> str:
    """Hash falso para verificar siempre una contraseña y que el tiempo de respuesta
    no revele si el correo existe."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = _hasher.hash(str(uuid.uuid4()))
    return _dummy_hash
