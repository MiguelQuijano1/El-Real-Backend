import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.rate_limit import client_ip, limiter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

GLOBAL_LIMIT_PER_MINUTE = 120


def create_app() -> FastAPI:
    settings = get_settings()
    docs = None if settings.is_production else "/docs"
    app = FastAPI(
        title="ERP EnterpriseCloud API",
        version="1.0.0",
        docs_url=docs,
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    @app.middleware("http")
    async def security_headers_and_limit(request: Request, call_next):
        if not limiter.hit(f"global:{client_ip(request)}", GLOBAL_LIMIT_PER_MINUTE, 60):
            return JSONResponse({"statusCode": 429, "message": "Too many requests", "path": request.url.path}, status_code=429)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        if settings.is_production:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    # CORS va al final para quedar como middleware más externo y responder también a los errores 429.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    register_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
