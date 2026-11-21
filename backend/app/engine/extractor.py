from __future__ import annotations

import base64
import io
import json
import logging
import re
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx
import pdfplumber
import pypdfium2 as pdfium
from PIL import Image

from app.core.config import Settings, get_settings
from app.models.models import (
    ExtractedField,
    ExtractedInvoiceFields,
    ExtractionMetadata,
    InvoiceTotals,
    LLMExtractedInvoice,
    LineItem,
    NormalizedInvoice,
)

logger = logging.getLogger(__name__)
_money = re.compile(r'-?\s*([\d,]+(?:\.\d{1,4})?)')
_DATE_RE = re.compile(r'\b(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4}|\d{1,2}\s+[A-Za-z]+\s+\d{4})\b')
_PROMPT = """Carefully inspect the entire invoice and return one JSON object with every schema field:
invoice_number, vendor_name, vendor_tax_id, invoice_date, due_date, po_number, currency,
subtotal, tax_rate_percent, tax_amount, total, line_items, field_confidences, and extraction_notes.
Read all visible header and footer text and every line item. Use null only when a value is absent;
include every field even when null. Each line item must include description, quantity, unit_price,
and amount. Use only values visibly printed in the document. Never calculate or correct arithmetic:
copy the printed subtotal, tax, total, quantity, unit price, and line amount literally. Parse tax rates
and amounts separately, including GST, CGST, SGST and VAT. Normalize dates to YYYY-MM-DD and
currency to an ISO 4217 code. Include a confidence from 0 to 1 for each extracted field and short
notes for ambiguity. A PO reference may be implied by a header or note, but say when inferred."""


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    raw = str(value).strip().replace('(', '-').replace(')', '')
    match = _money.search(raw)
    if not match:
        raise InvalidOperation(str(value))
    return Decimal(match.group(1).replace(',', ''))


def _field(value: Any, source: str, confidence: float = 0.95, page: int = 1) -> ExtractedField:
    return ExtractedField(value=value, confidence=Decimal(str(round(confidence * 100, 2))), source_text=source or None, page=page)


def _normalize_date(value: str | None) -> str | None:
    if not value:
        return None
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%m/%d/%Y', '%d/%m/%Y', '%d %B %Y', '%d %b %Y'):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return value


class InvoiceExtractor:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @staticmethod
    def _read_pdf(source: str | Path | bytes) -> tuple[str, list[list[list[str]]]]:
        pdf = pdfplumber.open(io.BytesIO(source) if isinstance(source, bytes) else str(source))
        with pdf:
            pages = [page.extract_text() or '' for page in pdf.pages]
            tables = [table for page in pdf.pages for table in (page.extract_tables() or [])]
        return '\n'.join(pages), tables

    @staticmethod
    def _render_pdf(source: bytes) -> list[str]:
        document = pdfium.PdfDocument(source)
        images = []
        for index in range(min(2, len(document))):
            bitmap = document[index].render(scale=150 / 72)
            image = bitmap.to_pil().convert('RGB')
            if image.width > 1024:
                height = round(image.height * 1024 / image.width)
                image = image.resize((1024, height), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format='JPEG', quality=82, optimize=True, progressive=True)
            images.append('data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii'))
        return images

    @staticmethod
    def _content(result: dict[str, Any]) -> Any:
        choices = result.get('choices') or []
        if choices:
            return (choices[0].get('message') or {}).get('content')
        nested = result.get('result')
        if isinstance(nested, dict):
            return nested.get('response') or nested.get('content')
        return result.get('response')

    @staticmethod
    def _parse_response(content: Any) -> LLMExtractedInvoice:
        if isinstance(content, list):
            content = ''.join(item.get('text', '') for item in content if isinstance(item, dict))
        if isinstance(content, str):
            content = re.sub(r'^\s*```(?:json)?\s*|\s*```\s*$', '', content.strip(), flags=re.I)
            content = json.loads(content)
        if not isinstance(content, dict):
            raise ValueError('Workers AI returned no JSON object')
        return LLMExtractedInvoice.model_validate(content)

    async def _call_ai(self, model: str, text: str, images: list[str] | None = None) -> LLMExtractedInvoice:
        token = self.settings.workers_ai_token
        account_id = self.settings.cloudflare_account_id
        if not token or not account_id:
            raise RuntimeError('Workers AI credentials are not configured')
        user_content: Any = text
        if images:
            user_content = [{'type': 'text', 'text': text}]
            user_content.extend({'type': 'image_url', 'image_url': {'url': image}} for image in images)
        messages = [{'role': 'system', 'content': _PROMPT}, {'role': 'user', 'content': user_content}]
        url = f'https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions'
        schema = LLMExtractedInvoice.model_json_schema()
        payload = {'model': model, 'messages': messages, 'temperature': 0, 'max_tokens': 3000,
                   'response_format': {'type': 'json_schema', 'json_schema': {'name': 'invoice_extraction', 'schema': schema}}}
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            response = await client.post(url, headers={'Authorization': f'Bearer {token}'}, json=payload)
            if response.is_error and response.status_code not in (400, 422):
                raise RuntimeError(f'Workers AI returned HTTP {response.status_code}')
            try:
                response.raise_for_status()
                return self._parse_response(self._content(response.json()))
            except Exception as exc:
                logger.warning('Workers AI schema response could not be used; retrying JSON mode: %s', exc)
                payload['response_format'] = {'type': 'json_object'}
                retry = await client.post(url, headers={'Authorization': f'Bearer {token}'}, json=payload)
                retry.raise_for_status()
                return self._parse_response(self._content(retry.json()))

    async def _ai_extract(self, text: str, images: list[str] | None) -> tuple[LLMExtractedInvoice, str, str]:
        if images:
            models = [self.settings.cf_ai_vision_model, self.settings.cf_ai_fallback_vision_model]
        else:
            models = [self.settings.cf_ai_text_model]
        errors = []
        for model in dict.fromkeys(models):
            try:
                data = await self._call_ai(model, _PROMPT + ('\n\nDOCUMENT TEXT:\n' + text if text else ''), images)
                missing = [key for key in ('invoice_number', 'vendor_name', 'total') if getattr(data, key) is None]
                if not data.line_items:
                    missing.append('line_items')
                low_confidence = [key for key in ('invoice_number', 'vendor_name', 'total', 'line_items')
                                  if data.field_confidences.get(key, 0.9) < 0.7]
                if images and (missing or low_confidence):
                    errors.append(ValueError(f'incomplete extraction; missing={missing}, low_confidence={low_confidence}'))
                    logger.warning('Workers AI returned incomplete invoice fields from %s (missing=%s, low_confidence=%s); trying the next configured model', model, missing, low_confidence)
                    continue
                return data, model, 'cf_workers_ai_vision' if images else 'cf_workers_ai_text'
            except Exception as exc:
                logger.warning('Workers AI extraction failed for %s: %s', model, exc)
                errors.append(exc)
        raise RuntimeError('All configured Workers AI models failed') from errors[-1]

    @staticmethod
    def _as_fields(data: LLMExtractedInvoice) -> ExtractedInvoiceFields:
        confidence = data.field_confidences
        po_number = data.po_number
        if po_number and po_number.strip().casefold() in {'not provided', 'none', 'n/a', 'na', 'null'}:
            po_number = None
        values = {
            'invoice_number': data.invoice_number, 'vendor_name': data.vendor_name,
            'vendor_tax_id': data.vendor_tax_id, 'invoice_date': data.invoice_date,
            'due_date': data.due_date, 'purchase_order_number': po_number,
            'currency': data.currency or 'USD', 'subtotal': data.subtotal,
            'tax': data.tax_amount, 'total': data.total,
            'tax_rate_percent': data.tax_rate_percent,
        }
        fields = ExtractedInvoiceFields()
        for key, value in values.items():
            if value is not None:
                c = float(confidence.get(key, 0.9))
                if c > 1:
                    c /= 100
                setattr(fields, key, _field(value, 'Cloudflare Workers AI', c))
        fields.line_items = [
            _field({'description': item.description, 'quantity': item.quantity, 'unit_price': item.unit_price,
                    'amount': item.amount}, 'Cloudflare Workers AI', float(confidence.get('line_items', 0.9)))
            for item in data.line_items if item.quantity is not None and item.unit_price is not None
        ]
        return fields

    @staticmethod
    def _fill_text_gaps(fields: ExtractedInvoiceFields, text: str, tables: list[list[list[str]]]) -> bool:
        deterministic = InvoiceExtractor._regex_extract(text, tables)
        filled = False
        for name in fields.__class__.model_fields:
            current = getattr(fields, name)
            candidate = getattr(deterministic, name)
            if (current is None or current == []) and candidate is not None and candidate != []:
                setattr(fields, name, candidate)
                filled = True
        return filled

    @staticmethod
    def _regex_extract(text: str, tables: list[list[list[str]]]) -> ExtractedInvoiceFields:
        def labeled(pattern: str) -> tuple[str, str] | None:
            found = list(re.finditer(pattern, text, re.I | re.M))
            if not found:
                return None
            match = found[-1]
            return match.group(1).strip(), match.group(0).strip()

        fields = ExtractedInvoiceFields()
        labels = {
            'invoice_number': r'^\s*(?:invoice(?:\s*(?:number|no\.?))?|inv\.?\s*#?)\s*[:#-]\s*([^\n]+)',
            'vendor_name': r'^\s*(?:vendor|supplier|sold\s+by)\s*[:#-]\s*([^\n]+)',
            'vendor_tax_id': r'^\s*(?:tax\s*id|ein|gstin|vat\s*(?:id|number)?)\s*[:#-]\s*([^\n]+)',
            'invoice_date': r'^\s*(?:invoice\s+date|date\s+issued)\s*[:#-]\s*([^\n]+)',
            'due_date': r'^\s*(?:due\s+date|payment\s+due)\s*[:#-]\s*([^\n]+)',
            'purchase_order_number': r'^\s*(?:purchase\s*order|p\.?o\.?\s*(?:number|no\.?|#)?)\s*[:#-]\s*([^\n]+)',
        }
        for key, pattern in labels.items():
            item = labeled(pattern)
            if item:
                value, source = item
                if key == 'purchase_order_number' and value.lower() in {'not provided', 'none', 'n/a'}:
                    continue
                if key in ('invoice_date', 'due_date'):
                    value = next(iter(_DATE_RE.findall(value)), value)
                    value = _normalize_date(value) or value
                setattr(fields, key, _field(value, source, 0.72))

        for key, pattern in {
            'subtotal': r'^\s*subtotal\s*[:#-]?\s*([^\n]+)',
            'tax': r'^\s*(?:tax|gst|vat)(?:\s*\([^)]*\))?\s*[:#-]?\s*([^\n]+)',
            'total': r'^\s*(?:grand\s+total|total\s+due|total)\s*[:#-]?\s*([^\n]+)',
        }.items():
            item = labeled(pattern)
            if item:
                value, source = item
                try:
                    setattr(fields, key, _field(_decimal(value), source, 0.72))
                    if key == 'tax':
                        rate = re.search(r'\((\d+(?:\.\d+)?)\s*%\)', source)
                        if rate:
                            fields.tax_rate_percent = _field(Decimal(rate.group(1)), source, 0.72)
                except InvalidOperation:
                    pass
        symbol = re.search(r'₹|\bINR\b|\bRs\.?\s*|€|\bEUR\b|£|\bGBP\b|\bUSD\b|\$', text, re.I)
        currency = 'USD'
        if symbol:
            marker = symbol.group().upper()
            currency = 'INR' if marker in ('₹', 'INR') or marker.startswith('RS') else 'EUR' if marker in ('€', 'EUR') else 'GBP' if marker in ('£', 'GBP') else 'USD'
        fields.currency = _field(currency, symbol.group() if symbol else '', 0.7)
        for table in tables:
            for row in table[1:]:
                cells = [str(cell or '').strip() for cell in row]
                if len(cells) < 4 or not cells[0] or cells[0].lower() in {'subtotal', 'tax', 'total'}:
                    continue
                try:
                    values = {'description': cells[0], 'quantity': _decimal(cells[1]),
                              'unit_price': _decimal(cells[2]), 'amount': _decimal(cells[3])}
                    fields.line_items.append(_field(values, ' | '.join(cells), 0.7))
                except InvalidOperation:
                    continue
        return fields

    async def extract(self, source: str | Path | bytes, file_name: str | None = None) -> tuple[ExtractedInvoiceFields, str, list[list[list[str]]], ExtractionMetadata, LLMExtractedInvoice | None, list[str]]:
        started = time.perf_counter()
        suffix = Path(file_name or (str(source) if not isinstance(source, bytes) else 'invoice.pdf')).suffix.lower()
        images: list[str] | None = None
        tables: list[list[list[str]]] = []
        text = ''
        scanned = suffix in {'.png', '.jpg', '.jpeg', '.webp'}
        if suffix == '.pdf' or not suffix:
            try:
                text, tables = self._read_pdf(source)
            except Exception:
                logger.warning('PDF text extraction failed; trying scanned-document path')
            scanned = len(text.strip()) < 30
            if scanned:
                try:
                    images = self._render_pdf(source if isinstance(source, bytes) else Path(source).read_bytes())
                except Exception:
                    logger.exception('Could not render scanned PDF pages')
        elif scanned:
            mime = 'image/jpeg' if suffix in {'.jpg', '.jpeg'} else 'image/webp' if suffix == '.webp' else 'image/png'
            raw = source if isinstance(source, bytes) else Path(source).read_bytes()
            images = [f'data:{mime};base64,' + base64.b64encode(raw).decode('ascii')]
        ai_data = None
        try:
            if scanned and not images:
                raise RuntimeError('Scanned document could not be rendered for Vision extraction')
            ai_data, model, method = await self._ai_extract(text, images)
            fields = self._as_fields(ai_data)
            notes = ai_data.extraction_notes
            if not scanned and self._fill_text_gaps(fields, text, tables):
                notes.append('Missing digital text fields were supplemented from the PDF text and tables.')
        except Exception as exc:
            logger.warning('Using deterministic extraction fallback: %s', exc)
            fields = self._regex_extract(text, tables)
            model = None
            method = 'vision_fallback' if scanned or images else 'pdfplumber_fallback'
            notes = ([f'Cloudflare Vision extraction failed; image text was not processed. {exc}'] if scanned or images else
                     ['Cloudflare text extraction failed; deterministic PDF text extraction was used.'])
        metadata = ExtractionMetadata(extraction_method=method, model_used=model,
                                      extraction_latency_ms=round((time.perf_counter() - started) * 1000),
                                      is_scanned_document=scanned)
        return fields, text, tables, metadata, ai_data, notes

    def normalize(self, fields: ExtractedInvoiceFields, source_file_name: str | None = None) -> NormalizedInvoice:
        missing = [key for key in ('invoice_number', 'vendor_name') if not getattr(fields, key)]
        if missing:
            raise ValueError('Missing required invoice fields: ' + ', '.join(missing))

        def date_value(field):
            if not field:
                return None
            try:
                return datetime.fromisoformat(str(field.value))
            except ValueError:
                return None

        items = []
        for field in fields.line_items:
            value = field.value
            items.append(LineItem(description=str(value.get('description', '')), quantity=Decimal(str(value['quantity'])),
                                  unit_price=Decimal(str(value['unit_price'])), amount=Decimal(str(value['amount'])) if value.get('amount') is not None else None))
        subtotal = Decimal(str(fields.subtotal.value)) if fields.subtotal else sum((item.amount if item.amount is not None else item.quantity * item.unit_price for item in items), Decimal('0'))
        tax = Decimal(str(fields.tax.value)) if fields.tax else Decimal('0')
        total = Decimal(str(fields.total.value)) if fields.total else subtotal + tax
        return NormalizedInvoice(invoice_number=str(fields.invoice_number.value), invoice_date=date_value(fields.invoice_date),
                                 due_date=date_value(fields.due_date), vendor_name=str(fields.vendor_name.value),
                                 vendor_tax_id=str(fields.vendor_tax_id.value) if fields.vendor_tax_id else None,
                                 purchase_order_number=str(fields.purchase_order_number.value) if fields.purchase_order_number else None,
                                 line_items=items, totals=InvoiceTotals(subtotal=subtotal, tax=tax, total=total,
                                                                        currency=str(fields.currency.value if fields.currency else 'USD')),
                                 source_file_name=source_file_name)
