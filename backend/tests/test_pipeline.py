from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

import pytest
import pdfplumber

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from app.api import routes
from app.engine.decision_arbiter import DecisionArbiter
from app.engine.extractor import InvoiceExtractor
from app.engine.pipeline import InvoiceProcessingEngine
from app.models.models import (
    AuditTrace, DecisionStatus, ExtractedField, ExtractedInvoiceFields, ExtractionMetadata,
    InvoiceStatus, LLMExtractedInvoice, ProcessRequest, ProcessResponse, ReviewRequest,
    ValidationCheck, ValidationStatus,
)
from scripts import generate_invoice_samples


class SQLiteD1:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript((ROOT / 'migrations/0001_schema.sql').read_text())
        self.db.executescript((ROOT / 'migrations/0002_seed.sql').read_text())
        self.db.executescript((ROOT / 'migrations/0003_hybrid_and_hitl.sql').read_text())

    async def query(self, sql, params=None):
        return [dict(row) for row in self.db.execute(sql, params or []).fetchall()]

    async def batch(self, statements):
        self.db.execute('BEGIN')
        try:
            result = []
            for sql, params in statements:
                cursor = self.db.execute(sql, params or [])
                result.append({'changes': cursor.rowcount})
            self.db.commit()
            return result
        except Exception:
            self.db.rollback()
            raise

    async def execute(self, sql, params=None):
        cursor = self.db.execute(sql, params or [])
        self.db.commit()
        return {'changes': cursor.rowcount}


def ef(value, confidence=0.95):
    return ExtractedField(value=value, confidence=Decimal(str(confidence * 100)))


def invoice_fields(invoice_number, vendor, po, quantity, unit_price, subtotal, tax, total,
                   currency='USD', date='2026-03-15', tax_rate=8.25):
    line_amount = Decimal(str(quantity)) * Decimal(str(unit_price))
    return ExtractedInvoiceFields(
        invoice_number=ef(invoice_number), vendor_name=ef(vendor), invoice_date=ef(date),
        purchase_order_number=ef(po) if po else None, currency=ef(currency),
        subtotal=ef(Decimal(str(subtotal))), tax_rate_percent=ef(Decimal(str(tax_rate))),
        tax=ef(Decimal(str(tax))), total=ef(Decimal(str(total))),
        line_items=[ef({'description': 'Enterprise cloud licenses' if po == 'PO-8820' else
                        'Office paper boxes' if po == 'PO-9912' else
                        'Server racks' if po == 'PO-4410' else 'Industrial replacement parts',
                        'quantity': Decimal(str(quantity)), 'unit_price': Decimal(str(unit_price)),
                        'amount': line_amount})],
    )


class FakeExtractor:
    def __init__(self, fields):
        self.fields = fields

    async def extract(self, source, file_name=None):
        raw = {
            'invoice_number': self.fields.invoice_number.value if self.fields.invoice_number else None,
            'vendor_name': self.fields.vendor_name.value if self.fields.vendor_name else None,
            'vendor_tax_id': self.fields.vendor_tax_id.value if self.fields.vendor_tax_id else None,
            'invoice_date': self.fields.invoice_date.value if self.fields.invoice_date else None,
            'due_date': None,
            'po_number': self.fields.purchase_order_number.value if self.fields.purchase_order_number else None,
            'currency': self.fields.currency.value if self.fields.currency else 'USD',
            'subtotal': self.fields.subtotal.value if self.fields.subtotal else None,
            'tax_rate_percent': self.fields.tax_rate_percent.value if self.fields.tax_rate_percent else None,
            'tax_amount': self.fields.tax.value if self.fields.tax else None,
            'total': self.fields.total.value if self.fields.total else None,
            'line_items': [item.value for item in self.fields.line_items],
            'field_confidences': {key: 0.95 for key in ('invoice_number', 'vendor_name', 'total', 'line_items')},
        }
        return self.fields, '', [], ExtractionMetadata(extraction_method='pdfplumber_fallback'), LLMExtractedInvoice.model_validate(raw), []

    def normalize(self, fields, file_name=None):
        return InvoiceExtractor().normalize(fields, file_name)


def run_engine(db, fields):
    return asyncio.run(InvoiceProcessingEngine(d1=db, extractor=FakeExtractor(fields)).process(b'', 'test.pdf'))


@pytest.fixture
def db():
    instance = SQLiteD1()
    yield instance
    instance.db.close()


def test_happy_path_approves_and_checks_math(db):
    fields = invoice_fields('INV-TEST-1', 'Acme Corp', 'PO-8820', 5, 2000, 10000, 825, 10825)
    result = run_engine(db, fields)
    assert result.decision.decision == DecisionStatus.APPROVED
    checks = {check.name: check.status for check in result.decision.validation_checks}
    assert checks['subtotal_math'] == ValidationStatus.PASS
    assert checks['total_math'] == ValidationStatus.PASS


def test_price_creep_fails_only_po_unit_price_check(db):
    fields = invoice_fields('INV-TEST-2', 'Global Supplies', 'PO-9912', 10, 13, 130, 10.73, 140.73)
    result = run_engine(db, fields)
    checks = {check.name: check for check in result.decision.validation_checks}
    assert result.decision.decision == DecisionStatus.MANUAL_REVIEW
    assert checks['subtotal_math'].status == ValidationStatus.PASS
    assert checks['total_math'].status == ValidationStatus.PASS
    assert checks['po_line_1'].status == ValidationStatus.FAIL
    assert '+8.33%' in checks['po_line_1'].message
    assert '$13.00 vs PO $12.00' in checks['po_line_1'].message


def test_partial_billing_warns_and_preserves_remaining_balance(db):
    fields = invoice_fields('INV-TEST-3', 'Apex Engineering', 'PO-4410', 2, 5000, 10000, 825, 10825)
    result = run_engine(db, fields)
    assert result.decision.decision == DecisionStatus.APPROVED_WITH_WARNING
    partial = next(check for check in result.decision.validation_checks if check.name == 'partial_billing')
    assert 'USD 40,000.00 remains after this invoice' in partial.message
    assert 'USD 40,000.00 remains' in result.decision.executive_summary


def test_duplicate_invoice_is_rejected(db):
    fields = invoice_fields('INV-7729', 'Novas Industrial Inc.', 'PO-7729', 15, 300, 4500, 371.25, 4871.25)
    result = run_engine(db, fields)
    assert result.decision.decision == DecisionStatus.REJECTED
    assert result.decision.duplicate_evidence


def test_duplicate_is_rejected_first_after_reset(db, monkeypatch):
    monkeypatch.setattr(routes, 'd1', db)
    asyncio.run(routes.reset())
    fields = invoice_fields('INV-7729', 'Novas Industrial Inc.', 'PO-7729', 15, 300, 4500, 371.25, 4871.25)
    result = run_engine(db, fields)
    assert result.decision.decision == DecisionStatus.REJECTED
    assert result.decision.duplicate_evidence[0].invoice_run_id == 'RUN-HIST-7729'


def test_missing_po_over_500_requires_manager(db):
    fields = invoice_fields('INV-TEST-5', 'Stealth Tech Labs', None, 1, 8400, 8400, 693, 9093)
    result = run_engine(db, fields)
    assert result.decision.decision == DecisionStatus.MANUAL_REVIEW
    assert 'exceeds $500' in result.decision.reason


def test_scanned_image_pdf_uses_vision_and_has_no_text_layer(tmp_path, monkeypatch):
    monkeypatch.setattr(generate_invoice_samples, 'OUTPUT_DIR', tmp_path)
    sample = generate_invoice_samples.create_scanned_pdf()
    data = sample.read_bytes()
    text, _ = InvoiceExtractor._read_pdf(data)
    assert len(text.strip()) < 30
    extractor = InvoiceExtractor()
    requested = {}

    async def fake_ai(document_text, images):
        requested['images'] = images
        invoice = LLMExtractedInvoice(invoice_number='INV-SCAN', vendor_name='Acme Corp', po_number='PO-8820', total=10825)
        return invoice, '@cf/meta/llama-4-scout-17b-16e-instruct', 'cf_workers_ai_vision'

    monkeypatch.setattr(extractor, '_ai_extract', fake_ai)
    fields, _, _, metadata, result, _ = asyncio.run(extractor.extract(data, sample.name))
    assert metadata.is_scanned_document is True
    assert metadata.extraction_method == 'cf_workers_ai_vision'
    assert requested['images'][0].startswith('data:image/png;base64,')
    assert fields.invoice_number.value == 'INV-SCAN'
    assert result.invoice_number == 'INV-SCAN'


def test_generated_samples_include_key_fields_and_round_tax_half_up(tmp_path, monkeypatch):
    monkeypatch.setattr(generate_invoice_samples, 'OUTPUT_DIR', tmp_path)
    for scenario, invoice, vendor, po, items in generate_invoice_samples.SCENARIOS:
        sample = generate_invoice_samples.create_pdf(scenario, invoice, vendor, po, items)
        with pdfplumber.open(sample) as pdf:
            text = '\n'.join(page.extract_text() or '' for page in pdf.pages)
        if scenario in {'happy_path', 'partial_billing'}:
            assert 'Vendor Tax ID:' in text
            assert 'Due date: 2026-10-15' in text
        if scenario == 'price_creep':
            assert 'Tax (8.25%) $10.73' in text
            assert 'Total $140.73' in text


def test_indian_invoice_regex_extracts_inr_and_unknown_vendor_routes_to_review(db):
    text = 'Invoice Number: INV-2026-003\nVendor: BRAND MANAGEMENT\nInvoice Date: 15 March 2026\nRs. 800.00\nTotal: Rs. 800.00'
    tables = [[['Description', 'Quantity', 'Unit price', 'Amount'], ['Brand management services', '1', 'Rs. 800.00', 'Rs. 800.00']]]
    fields = InvoiceExtractor._regex_extract(text, tables)
    invoice = InvoiceExtractor().normalize(fields, 'invoice.pdf')
    assert invoice.invoice_number == 'INV-2026-003'
    assert invoice.invoice_date.date().isoformat() == '2026-03-15'
    assert invoice.vendor_name == 'BRAND MANAGEMENT'
    assert invoice.totals.currency == 'INR'
    assert invoice.totals.total == Decimal('800.00')
    result = run_engine(db, fields)
    assert result.decision.decision == DecisionStatus.MANUAL_REVIEW


def test_response_boundary_accepts_fenced_json_and_currency_strings():
    parsed = InvoiceExtractor._parse_response('```json\n{"invoice_number":"I-1","currency":"INR","subtotal":"Rs. 800.00","tax_amount":"0","total":"800.00","line_items":[{"description":"service","quantity":"1","unit_price":"Rs. 800.00","amount":"800.00"}]}\n```')
    assert parsed.subtotal == 800
    assert parsed.line_items[0].unit_price == 800
    assert parsed.currency == 'INR'
    assert LLMExtractedInvoice(po_number='Not provided').po_number is None
    assert InvoiceExtractor._parse_response({'invoice_number': 'I-2', 'extraction_notes': 'One ambiguous field'}).extraction_notes == ['One ambiguous field']


def test_failed_custom_upload_is_saved_and_review_override_resets_po(db, monkeypatch):
    monkeypatch.setattr(routes, 'd1', db)
    fields = invoice_fields('INV-HITL', 'Global Supplies', 'PO-9912', 10, 13, 130, 10.73, 140.73)
    result = run_engine(db, fields)
    request = ProcessRequest(file_name='price-creep.pdf', scenario={'key': 'price_creep'})
    asyncio.run(routes._persist_run(result, 'price-creep.pdf', request, None))
    row = asyncio.run(db.query('SELECT * FROM invoice_runs WHERE run_id = ?', [result.run_id]))[0]
    assert row['decision'] == 'MANUAL_REVIEW'
    assert row['po_credited'] == 0
    reviewed = asyncio.run(routes.review_run(result.run_id, ReviewRequest(action='APPROVE', reviewer_name='AP Manager', reviewer_note='Confirmed with supplier quote.')))
    assert reviewed.human_decision == 'APPROVED_OVERRIDE'
    assert reviewed.reviewer_note == 'Confirmed with supplier quote.'
    assert asyncio.run(db.query('SELECT amount_invoiced FROM purchase_orders WHERE po_number = ?', ['PO-9912']))[0]['amount_invoiced'] == 130
    audit = asyncio.run(routes.audit(result.run_id))
    assert audit['human_review']['reviewer_name'] == 'AP Manager'
    assert audit['human_review']['action'] == 'APPROVE'

    missing = ProcessResponse(run_id='missing-fields', status=InvoiceStatus.FAILED,
                               decision=DecisionArbiter().decide([ValidationCheck(name='missing_invoice_number', status=ValidationStatus.FAIL, message='Missing')], missing_fields=['invoice_number']),
                               trace=AuditTrace(trace_id='trace-missing', run_id='missing-fields'),
                               extraction_metadata=ExtractionMetadata(extraction_method='pdfplumber_fallback'),
                               extracted_fields=LLMExtractedInvoice(vendor_name='Unknown Supplier'))
    asyncio.run(routes._persist_run(missing, 'unreadable.pdf', None, None))
    saved = asyncio.run(routes.history(limit=20, offset=0, decision=None))
    fallback = next(run for run in saved if run.run_id == 'missing-fields')
    assert fallback.display_id == 'unreadable.pdf'
    assert fallback.vendor_display == 'Unknown Supplier'

    asyncio.run(routes.reset())
    baseline = asyncio.run(db.query('SELECT run_id, invoice_number FROM invoice_runs'))
    assert baseline == [{'run_id': 'RUN-HIST-7729', 'invoice_number': 'INV-7729'}]
    assert asyncio.run(db.query('SELECT amount_invoiced, status FROM purchase_orders WHERE po_number = ?', ['PO-7729']))[0] == {'amount_invoiced': 4500.0, 'status': 'closed'}


@pytest.mark.asyncio
@pytest.mark.skipif(not (os.getenv('CLOUDFLARE_ACCOUNT_ID') and (os.getenv('CLOUDFLARE_AI_API_TOKEN') or os.getenv('CLOUDFLARE_API_TOKEN') or os.getenv('CLOUDFLARE_API_KEY'))), reason='Cloudflare Workers AI credentials not set')
async def test_optional_live_workers_ai_smoke():
    from app.core.config import Settings

    settings = Settings(_env_file=None)
    result = await InvoiceExtractor(settings)._call_ai(settings.cf_ai_text_model,
        'Return the invoice_number TEST-1, vendor_name Acme Corp, and total 12.00. Return null for absent fields.')
    assert result.invoice_number == 'TEST-1'


@pytest.mark.asyncio
@pytest.mark.skipif(not (os.getenv('CLOUDFLARE_ACCOUNT_ID') and (os.getenv('CLOUDFLARE_AI_API_TOKEN') or os.getenv('CLOUDFLARE_API_TOKEN') or os.getenv('CLOUDFLARE_API_KEY'))), reason='Cloudflare Workers AI credentials not set')
async def test_optional_live_workers_ai_vision_smoke():
    from app.core.config import Settings

    settings = Settings(_env_file=None)
    source = (ROOT / 'backend/generated_samples/scanned_invoice.pdf').read_bytes()
    fields, _, _, metadata, extracted, _ = await InvoiceExtractor(settings).extract(source, 'scanned_invoice.pdf')
    assert metadata.is_scanned_document is True
    assert metadata.extraction_method == 'cf_workers_ai_vision'
    assert extracted is not None
    assert extracted.invoice_number == 'INV-2026-1006'
    assert extracted.po_number == 'PO-9912'
    assert extracted.total == 129.9
    assert fields.line_items[0].value['description'] == 'Office paper boxes'
