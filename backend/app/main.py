from contextlib import asynccontextmanager
import logging
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.api import router
from app.api.routes import d1


@asynccontextmanager
async def lifespan(_: FastAPI):
    if d1.configured:
        await d1.ensure_schema()
    yield


settings = get_settings()
logger = logging.getLogger(__name__)

app = FastAPI(
    title='Zamp AP Invoice Intelligence API',
    description='Backend foundation for explainable invoice processing workflows.',
    version='0.1.0',
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
    allow_headers=['*'],
)

app.include_router(router)


@app.exception_handler(Exception)
async def unexpected_error_handler(_, exc: Exception):
    logger.error('Unhandled API exception', exc_info=(type(exc), exc, exc.__traceback__))
    return JSONResponse(status_code=500, content={
        'detail': 'An unexpected server error occurred. Please try again.',
        'error_code': 'INTERNAL_ERROR',
    })


@app.get('/api/health', tags=['system'])
async def health() -> dict[str, Any]:
    return {
        'status': 'ok', 'environment': settings.environment,
        'ai_provider': 'cloudflare_workers_ai',
        'ai_configured': bool(settings.cloudflare_account_id and settings.workers_ai_token),
        'vision_model': settings.cf_ai_vision_model,
        'text_model': settings.cf_ai_text_model,
    }
