import os

# Variables mínimas para poder importar la app sin un .env real.
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-0123456789")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("TRUST_PROXY", "true")  # permite variar la IP con X-Forwarded-For en las pruebas
os.environ.setdefault("SEED_ADMIN_EMAIL", "admin@test.pe")
os.environ.setdefault("SEED_ADMIN_PASSWORD", "AdminPass-12345")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    from app.core.rate_limit import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture(scope="session")
def api():
    """Cliente HTTP contra un PostgREST real (TEST_POSTGREST_URL). Sin la variable, se omiten las pruebas de integración.

    Ejemplo: PostgREST local sobre una BD con database/001_schema.sql y 002_functions.sql aplicados:
        TEST_POSTGREST_URL=http://localhost:3001 pytest
    """
    url = os.environ.get("TEST_POSTGREST_URL")
    if not url:
        pytest.skip("TEST_POSTGREST_URL no definido: se omiten las pruebas de integración")

    from fastapi.testclient import TestClient
    from postgrest import SyncPostgrestClient

    from app.main import app
    from app.repositories.client import set_client
    from scripts import seed

    set_client(SyncPostgrestClient(url))
    seed.main()
    with TestClient(app) as client:
        yield client
