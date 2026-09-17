from typing import Any

import httpx

from app.core.config import Settings


class D1Client:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.cloudflare_account_id
            and self.settings.cloudflare_api_token
            and self.settings.d1_database_id
        )

    async def execute(self, sql: str, params: list[Any] | None = None) -> dict[str, Any]:
        if not self.configured:
            raise RuntimeError('Cloudflare D1 credentials are not configured')

        url = (
            f'https://api.cloudflare.com/client/v4/accounts/'
            f'{self.settings.cloudflare_account_id}/d1/database/'
            f'{self.settings.d1_database_id}/query'
        )
        headers = {
            'Authorization': f'Bearer {self.settings.cloudflare_api_token}',
            'Content-Type': 'application/json',
        }
        payload: dict[str, Any] = {'sql': sql}
        if params:
            payload['params'] = params

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                headers=headers,
                json=payload,
            )
            body = response.json()

        if response.is_error:
            raise RuntimeError(
                f'D1 HTTP {response.status_code}: '
                f'{body.get("errors", body)}'
            )

        if not body.get('success', False):
            raise RuntimeError(f'D1 query failed: {body.get("errors", body)}')
        return body

    async def query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        result = await self.execute(sql, params)
        return result.get('result', [{}])[0].get('results', [])
