import pytest

from app.core.config import duration_to_seconds
from app.core.rate_limit import SlidingWindowLimiter
from app.core.serialization import camel, to_camel
from app.security.catalog import ALL_MODULE_KEYS, PERMISSION_ACTIONS
from app.security.passwords import hash_password, verify_password
from app.security.tokens import create_access_token, decode_access_token, hash_token


def test_password_roundtrip_uses_argon2id():
    h = hash_password("una-clave-larga-123")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "una-clave-larga-123")
    assert not verify_password(h, "otra")
    assert not verify_password("no-es-un-hash", "x")


def test_token_roundtrip_and_tamper():
    token, _ = create_access_token("user-1", "sess-1")
    assert decode_access_token(token)["sub"] == "user-1"
    assert decode_access_token(token + "x") is None
    assert hash_token(token) != token


def test_duration():
    assert duration_to_seconds("8h") == 28800
    with pytest.raises(ValueError):
        duration_to_seconds("8 horas")


def test_camel():
    assert to_camel("role_type") == "roleType"
    assert camel([{"full_name": "x", "role": {"is_active": True}}]) == [{"fullName": "x", "role": {"isActive": True}}]


def test_rate_limiter_window():
    lim = SlidingWindowLimiter()
    assert all(lim.hit("k", 3, 60) for _ in range(3))
    assert not lim.hit("k", 3, 60)
    assert lim.hit("otra", 3, 60)


def test_catalog_has_no_duplicates():
    assert len(ALL_MODULE_KEYS) == len(set(ALL_MODULE_KEYS)) == 29
    assert PERMISSION_ACTIONS == ["view", "create", "edit", "delete", "approve"]
