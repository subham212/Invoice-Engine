from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pdfplumber

from app.models.models import ExtractedField, ExtractedInvoiceFields, LineItem, InvoiceTotals, NormalizedInvoice

_money = re.compile(r'[-$€£]?\s*([\d,]+(?:\.\d{1,4})?)')
_date = re.compile(r'\b(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4})\b')

def _dec(value: Any) -> Decimal:
    if isinstance(value, Decimal): return value
    match = _money.search(str(value).replace('(', '-'))
    if not match: raise InvalidOperation(str(value))
    return Decimal(match.group(1).replace(',', ''))

def _field(value: Any, source: str, confidence: int = 95, page: int = 1) -> ExtractedField:
    return ExtractedField(value=value, confidence=Decimal(confidence), source_text=source, page=page)

class InvoiceExtractor:
    def extract(self, source: str | Path | bytes) -> tuple[ExtractedInvoiceFields, str, list[list[list[str]]]]:
        pages: list[str] = []; tables: list[list[list[str]]] = []
        if isinstance(source, bytes):
            import io
            pdf = pdfplumber.open(io.BytesIO(source))
        else: pdf = pdfplumber.open(str(source))
        with pdf:
            for page in pdf.pages:
                pages.append(page.extract_text() or '')
                tables.extend(page.extract_tables() or [])
        text = '\n'.join(pages)
        def labeled(label: str) -> tuple[str, str] | None:
            matches = list(re.finditer(rf'(?im)^\s*{label}\s*[:#-]\s*([^\n]+)', text))
            if not matches: return None
            m = matches[-1]
            return (m.group(1).strip(), m.group(0).strip())
        fields = ExtractedInvoiceFields()
        for key, label in [('invoice_number','invoice'), ('vendor_name','vendor'), ('invoice_date','invoice date'), ('purchase_order_number','purchase order')]:
            found = labeled(label)
            if found:
                val, src = found
                if key == 'purchase_order_number' and val.lower() in {'not provided','none','n/a',''}: continue
                if key == 'invoice_number': val = re.sub(r'^date\s*[:#-]?\s*', '', val, flags=re.I).strip()
                if key == 'invoice_date':
                    dm = _date.search(val); val = dm.group(1) if dm else val
                setattr(fields, key, _field(val, src))
        for key, label in [('subtotal','subtotal'), ('tax','tax'), ('total','total')]:
            found = labeled(label)
            if found:
                val, src = found
                try: setattr(fields, key, _field(_dec(val), src))
                except InvalidOperation: pass
        currency = re.search(r'([$€£])', text)
        fields.currency = _field({'$':'USD','€':'EUR','£':'GBP'}.get(currency.group(1), 'USD'), currency.group(0) if currency else '', 85)
        line_items: list[ExtractedField] = []
        for table in tables:
            for row in table[1:]:
                cells = [str(c or '').strip() for c in row]
                if len(cells) < 4: continue
                try:
                    qty = _dec(cells[1]); price = _dec(cells[2]); amount = _dec(cells[3])
                    if qty >= 0 and price >= 0: line_items.append(_field({'description':cells[0], 'quantity':qty, 'unit_price':price, 'amount':amount}, ' | '.join(cells)))
                except InvalidOperation: continue
        fields.line_items = line_items
        return fields, text, tables

    def normalize(self, fields: ExtractedInvoiceFields, source_file_name: str | None = None) -> NormalizedInvoice:
        missing = [x for x in ('invoice_number','vendor_name') if not getattr(fields, x)]
        if missing: raise ValueError('Missing required invoice fields: ' + ', '.join(missing))
        def date_value(f):
            if not f: return None
            try: return datetime.fromisoformat(str(f.value).replace('/', '-'))
            except ValueError: return None
        items=[]
        for f in fields.line_items:
            v=f.value
            items.append(LineItem(description=str(v.get('description','')), quantity=Decimal(v['quantity']), unit_price=Decimal(v['unit_price']), amount=Decimal(v['amount'])))
        subtotal = Decimal(fields.subtotal.value) if fields.subtotal else sum((i.amount or i.quantity*i.unit_price for i in items), Decimal('0'))
        tax = Decimal(fields.tax.value) if fields.tax else Decimal('0')
        total = Decimal(fields.total.value) if fields.total else subtotal + tax
        return NormalizedInvoice(invoice_number=str(fields.invoice_number.value), invoice_date=date_value(fields.invoice_date), vendor_name=str(fields.vendor_name.value), purchase_order_number=str(fields.purchase_order_number.value) if fields.purchase_order_number else None, line_items=items, totals=InvoiceTotals(subtotal=subtotal, tax=tax, total=total, currency=str(fields.currency.value if fields.currency else 'USD')), source_file_name=source_file_name)
