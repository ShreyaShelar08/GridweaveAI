"""Application entry point: wires routers, middleware, logging and health check."""
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from . import models  # noqa: F401  (registers tables)
from .config import settings
from .database import Base, SessionLocal, engine
from .logging_config import setup_logging
from .routers import admin, auth, billing, contracts, ml, plants, profiles, readings, reports, webhooks

setup_logging()
logger = logging.getLogger("gridweave")
STARTED = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.auto_create_tables:  # quick local/test runs only; real databases use `alembic upgrade head`
        Base.metadata.create_all(bind=engine)
    logger.info("GridWeave backend started (env=%s, db=%s)", settings.environment, engine.dialect.name)
    yield


is_prod = settings.environment == "production"
app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan,
              docs_url=None if is_prod else "/docs", redoc_url=None, openapi_url=None if is_prod else "/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def request_logger(request: Request, call_next):
    rid, t0 = uuid.uuid4().hex[:8], time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled error rid=%s %s %s", rid, request.method, request.url.path)
        return JSONResponse({"detail": "Internal server error", "request_id": rid}, status_code=500)
    logger.info("%s %s -> %s (%.1f ms) rid=%s", request.method, request.url.path,
                response.status_code, (time.perf_counter() - t0) * 1000, rid)
    response.headers["X-Request-ID"] = rid
    return response


for r in (auth.router, profiles.router, plants.plants_router, plants.buyers_router, contracts.router,
          readings.router, billing.router, reports.router, webhooks.router, ml.router, admin.router):
    app.include_router(r)


@app.get("/health", tags=["Monitoring"])
def health():
    """Used by uptime monitors / the hosting platform."""
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        logger.exception("Health check: database unreachable")
        db_ok = False
    return JSONResponse({"status": "ok" if db_ok else "degraded", "database": db_ok,
                         "uptime_seconds": int(time.time() - STARTED)},
                        status_code=200 if db_ok else 503)
