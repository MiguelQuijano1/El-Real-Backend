"""Garantiza el "denegar por defecto": toda ruta debe declarar public / authenticated / require_permission."""
from fastapi.routing import APIRoute

from app.main import app


def iter_routes(router, prefix: str = ""):
    """Recorre todas las rutas, incluidas las de routers anidados (FastAPI recientes los resuelven de forma diferida)."""
    for route in router.routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
        elif hasattr(route, "original_router"):
            ctx = getattr(route, "include_context", None)
            yield from iter_routes(route.original_router, prefix + (getattr(ctx, "prefix", "") or ""))


def policies(dependant) -> list:
    found = []
    for dep in dependant.dependencies:
        policy = getattr(dep.call, "policy", None)
        if policy is not None:
            found.append(policy)
        found.extend(policies(dep))
    return found


ROUTES = list(iter_routes(app.router))


def test_routes_were_discovered():
    assert len(ROUTES) > 50  # evita que el test pase "en vacío"


def test_every_route_declares_an_access_policy():
    missing = [f"{sorted(r.methods)} {path}" for path, r in ROUTES if not policies(r.dependant)]
    assert not missing, f"Rutas sin política de acceso: {missing}"


def test_only_login_and_health_are_public():
    public = {path for path, r in ROUTES if "public" in policies(r.dependant)}
    assert public == {"/api/v1/auth/login", "/api/v1/health"}
