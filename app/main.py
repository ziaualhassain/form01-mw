from __future__ import annotations

import asyncio
import uuid
import logging

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.errors import ApiError, api_exception_handler, http_exception_handler, validation_exception_handler
from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import async_session_factory, get_db


configure_logging()
settings = get_settings()
logger = logging.getLogger(__name__)
payment_expiry_task: asyncio.Task | None = None

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url="/redoc" if settings.environment != "production" else None,
    openapi_url="/openapi.json" if settings.environment != "production" else None,
)


async def _payment_expiry_worker() -> None:
    while True:
        try:
            async with async_session_factory() as db:
                from app.services.orders import expire_pending_payments

                expired = await expire_pending_payments(db, settings.pending_payment_timeout_minutes)
                if expired:
                    logger.info("Expired %s pending payments and released inventory", expired)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Pending payment expiry failed: %s", exc)
        await asyncio.sleep(settings.payment_expiry_interval_seconds)


@app.on_event("startup")
async def start_payment_expiry_worker() -> None:
    global payment_expiry_task
    payment_expiry_task = asyncio.create_task(_payment_expiry_worker())


@app.on_event("shutdown")
async def stop_payment_expiry_worker() -> None:
    if payment_expiry_task is not None:
        payment_expiry_task.cancel()
        await asyncio.gather(payment_expiry_task, return_exceptions=True)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


app.include_router(v1_router, prefix="/api/v1")


@app.get("/health/live", tags=["health"])
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def ready(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Readiness check failed: %s", exc)
        return {"status": "not_ready"}


@app.exception_handler(ApiError)
async def handle_api_error(request: Request, exc: ApiError):
    return await api_exception_handler(request, exc)


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(request: Request, exc: StarletteHTTPException):
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
    return await validation_exception_handler(request, exc)


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "The request could not be processed.",
                "details": [],
                "request_id": getattr(request.state, "request_id", None),
            }
        },
    )
