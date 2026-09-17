from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote, urlparse

import httpx

from app.core.config import Settings


class SupabaseStorageClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def configured(self) -> bool:
        url = self.settings.supabase_url or ''
        return bool(
            urlparse(url).scheme in {'http', 'https'}
            and urlparse(url).netloc
            and self.settings.supabase_service_role_key
            and self.settings.supabase_storage_bucket
        )

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        if not self.configured:
            raise RuntimeError('Supabase Storage credentials are not configured')
        headers = {
            'Authorization': f'Bearer {self.settings.supabase_service_role_key}',
            'apikey': self.settings.supabase_service_role_key or '',
        }
        if content_type:
            headers['Content-Type'] = content_type
        return headers

    def _object_url(self, key: str) -> str:
        if not key or key.startswith('/') or '..' in Path(key).parts:
            raise ValueError('Storage object key must be a relative path')
        return (
            f"{(self.settings.supabase_url or '').rstrip('/')}/storage/v1/object/"
            f"{quote(self.settings.supabase_storage_bucket, safe='')}/{quote(key, safe='/')}"
        )

    def upload_file(self, key: str, data: bytes | BinaryIO, content_type: str = 'application/pdf') -> None:
        body = data if isinstance(data, bytes) else data.read()
        headers = self._headers(content_type)
        headers['x-upsert'] = 'true'
        response = httpx.post(self._object_url(key), headers=headers, content=body, timeout=30.0)
        response.raise_for_status()

    def download_file(self, key: str) -> bytes:
        response = httpx.get(self._object_url(key), headers=self._headers(), timeout=30.0)
        response.raise_for_status()
        return response.content

    def delete_file(self, key: str) -> None:
        response = httpx.delete(self._object_url(key), headers=self._headers(), timeout=30.0)
        response.raise_for_status()

    def generate_signed_url(self, key: str, expires_in: int = 900) -> str:
        if expires_in <= 0:
            raise ValueError('Signed URL expiry must be positive')
        url = (
            f"{(self.settings.supabase_url or '').rstrip('/')}/storage/v1/object/sign/"
            f"{quote(self.settings.supabase_storage_bucket, safe='')}/{quote(key, safe='/')}"
        )
        response = httpx.post(
            url,
            headers=self._headers('application/json'),
            json={'expiresIn': expires_in},
            timeout=30.0,
        )
        response.raise_for_status()
        path = response.json().get('signedURL')
        if not path:
            raise RuntimeError('Supabase Storage returned no signed URL')
        return path if path.startswith('http') else f"{(self.settings.supabase_url or '').rstrip('/')}{path}"

    # Compatibility alias for callers migrating from the R2 client.
    generate_presigned_url = generate_signed_url
