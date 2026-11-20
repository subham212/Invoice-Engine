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

    async def batch(self, statements: list[tuple[str, list[Any] | None]]) -> list[dict[str, Any]]:
        if not self.configured:
            raise RuntimeError('Cloudflare D1 credentials are not configured')
        url = (f'https://api.cloudflare.com/client/v4/accounts/{self.settings.cloudflare_account_id}'
               f'/d1/database/{self.settings.d1_database_id}/query')
        headers = {'Authorization': f'Bearer {self.settings.cloudflare_api_token}', 'Content-Type': 'application/json'}
        payload = {'batch': [{'sql': sql, 'params': params or []} for sql, params in statements]}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            body = response.json()
        if response.is_error or not body.get('success', False):
            raise RuntimeError(f'D1 batch failed: {body.get("errors", body)}')
        return body.get('result', [])

    async def query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        result = await self.execute(sql, params)
        return result.get('result', [{}])[0].get('results', [])

    async def ensure_schema(self) -> None:
        columns = {
            'invoice_runs': {
                'currency': "TEXT NOT NULL DEFAULT 'USD'", 'extraction_method': "TEXT NOT NULL DEFAULT 'pdfplumber_fallback'",
                'model_used': 'TEXT', 'extraction_latency_ms': 'INTEGER NOT NULL DEFAULT 0', 'is_scanned': 'INTEGER NOT NULL DEFAULT 0',
                'executive_summary': "TEXT NOT NULL DEFAULT ''", 'human_decision': 'TEXT', 'reviewer_name': 'TEXT',
                'reviewer_note': 'TEXT', 'reviewed_at': 'TEXT', 'invoice_json': 'TEXT', 'po_credited': 'INTEGER NOT NULL DEFAULT 0',
                'credit_amount': 'REAL NOT NULL DEFAULT 0',
            },
            'purchase_orders': {'base_amount_invoiced': 'REAL NOT NULL DEFAULT -1'},
        }
        for table, additions in columns.items():
            existing = {row['name'] for row in await self.query(f'PRAGMA table_info({table})')}
            if not existing:
                raise RuntimeError(f'D1 table {table} is missing; apply migrations/0001_schema.sql and migrations/0002_seed.sql first')
            statements = [(f'ALTER TABLE {table} ADD COLUMN {name} {definition}', [])
                          for name, definition in additions.items() if name not in existing]
            if statements:
                try:
                    await self.batch(statements)
                except RuntimeError:
                    existing = {row['name'] for row in await self.query(f'PRAGMA table_info({table})')}
                    if any(name not in existing for name in additions):
                        raise

        await self.execute('UPDATE purchase_orders SET base_amount_invoiced = amount_invoiced WHERE base_amount_invoiced < 0')
        schema = await self.query("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'purchase_orders'")
        if schema and 'partially_utilized' not in str(schema[0].get('sql', '')):
            statements = [
                ("CREATE TABLE purchase_orders_new (id TEXT PRIMARY KEY, po_number TEXT NOT NULL UNIQUE, vendor_id TEXT NOT NULL, total_amount REAL NOT NULL CHECK (total_amount >= 0), amount_invoiced REAL NOT NULL DEFAULT 0 CHECK (amount_invoiced >= 0), base_amount_invoiced REAL NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT 'USD', status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','partially_utilized','closed','cancelled')), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (vendor_id) REFERENCES vendors (id))", []),
                ('INSERT INTO purchase_orders_new SELECT id, po_number, vendor_id, total_amount, amount_invoiced, base_amount_invoiced, currency, status, created_at FROM purchase_orders', []),
                ("CREATE TABLE po_line_items_new (id TEXT PRIMARY KEY, po_id TEXT NOT NULL, item_code TEXT, description TEXT NOT NULL, quantity REAL NOT NULL CHECK (quantity > 0), unit_price REAL NOT NULL CHECK (unit_price >= 0), tax_rate REAL NOT NULL DEFAULT 0 CHECK (tax_rate >= 0), FOREIGN KEY (po_id) REFERENCES purchase_orders_new (id) ON DELETE CASCADE)", []),
                ('INSERT INTO po_line_items_new SELECT id, po_id, item_code, description, quantity, unit_price, tax_rate FROM po_line_items', []),
                ('DROP TABLE po_line_items', []),
                ('DROP TABLE purchase_orders', []),
                ('ALTER TABLE purchase_orders_new RENAME TO purchase_orders', []),
                ('ALTER TABLE po_line_items_new RENAME TO po_line_items', []),
                ('CREATE INDEX IF NOT EXISTS idx_purchase_orders_vendor_status ON purchase_orders (vendor_id, status)', []),
                ('CREATE INDEX IF NOT EXISTS idx_po_line_items_po_id ON po_line_items (po_id)', []),
                ('UPDATE purchase_orders SET status = CASE WHEN amount_invoiced >= total_amount THEN \'closed\' WHEN amount_invoiced > 0 THEN \'partially_utilized\' ELSE \'open\' END', []),
            ]
            try:
                await self.batch(statements)
            except RuntimeError:
                schema = await self.query("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'purchase_orders'")
                if not schema or 'partially_utilized' not in str(schema[0].get('sql', '')):
                    raise
