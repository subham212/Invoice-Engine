from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.api import router


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


settings = get_settings()

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


@app.get('/api/health', tags=['system'])
async def health() -> dict[str, str]:
    return {'status': 'ok', 'environment': settings.environment}
