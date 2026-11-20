from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import NoDecode
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore',
    )

    environment: str = 'development'
    allowed_origins: Annotated[list[str], NoDecode] = ['http://localhost:5173']

    cloudflare_account_id: str | None = None
    cloudflare_api_token: str | None = None
    d1_database_id: str | None = None
    cloudflare_ai_api_token: str | None = None
    cloudflare_api_key: str | None = None
    cf_ai_vision_model: str = '@cf/meta/llama-4-scout-17b-16e-instruct'
    cf_ai_fallback_vision_model: str = '@cf/google/gemma-4-26b-a4b-it'
    cf_ai_text_model: str = '@cf/meta/llama-3.3-70b-instruct-fp8-fast'

    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    supabase_storage_bucket: str = 'invoices'

    @field_validator('allowed_origins', mode='before')
    @classmethod
    def parse_allowed_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(',') if origin.strip()]
        return value

    @property
    def workers_ai_token(self) -> str | None:
        return self.cloudflare_ai_api_token or self.cloudflare_api_token or self.cloudflare_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()
