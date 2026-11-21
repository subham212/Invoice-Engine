from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers, UploadFile
from pypdf import PdfWriter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from app.api import routes
from app.core.config import Settings
from app.engine.extractor import InvoiceExtractor
from app.engine.pipeline import InvoiceProcessingEngine
from app.engine.extractor import _normalize_date
from app.models.models import ExtractedInvoiceFields, LLMExtractedInvoice, ProcessRequest, ReviewRequest
from test_pipeline import SQLiteD1, FakeExtractor, ef, invoice_fields, run_engine


@pytest.fixture
def edge_db():
    instance = SQLiteD1()
    yield instance
    instance.db.close()


def upload(data: bytes, filename='invoice.pdf') -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename,
                      headers=Headers({'content-type': 'application/pdf'}))


@pytest.mark.asyncio
async def test_empty_spoofed_and_oversized_uploads_are_rejected():
    with pytest.raises(HTTPException) as empty:
        await routes._read_upload(upload(b''))
    assert empty.value.status_code == 400 and empty.value.detail == 'Uploaded file is empty.'
    with pytest.raises(HTTPException) as spoofed:
        await routes._read_upload(upload(b'plain text', 'invoice.pdf'))
    assert spoofed.value.status_code == 400 and 'Invalid or unsupported' in spoofed.value.detail
    with pytest.raises(HTTPException) as oversized:
        await routes._read_upload(upload(b'%PDF-' + b'x' * (routes.MAX_UPLOAD_BYTES + 1)))
    assert oversized.value.status_code == 413 and oversized.value.detail == 'File exceeds the 10 MB upload limit.'


@pytest.mark.asyncio
async def test_corrupt_pdf_returns_clean_400():
    with pytest.raises(HTTPException) as error:
        await routes._process(b'%PDF-not-a-real-pdf', 'bad.pdf')
    assert error.value.status_code == 400
    assert error.value.detail == 'This PDF is password-protected or corrupted and cannot be read.'


@pytest.mark.asyncio
async def test_d1_outage_is_returned_as_safe_503(monkeypatch):
    class BrokenD1:
        async def query(self, *_args, **_kwargs): raise RuntimeError('private SQL text')
    monkeypatch.setattr(routes, 'd1', BrokenD1())
    with pytest.raises(HTTPException) as error:
        await routes.history()
    assert error.value.status_code == 503
    assert error.value.detail == 'Database temporarily unavailable. Please retry.'
    assert 'private SQL text' not in error.value.detail


@pytest.mark.asyncio
async def test_blank_document_is_saved_for_manual_review(edge_db, monkeypatch):
    monkeypatch.setattr(routes, 'd1', edge_db)
    result = await InvoiceProcessingEngine(d1=edge_db, extractor=FakeExtractor(ExtractedInvoiceFields())).process(b'', 'blank.pdf')
    assert result.decision.decision == 'MANUAL_REVIEW'
    assert result.decision.executive_summary == ('Uploaded document does not contain recognizable invoice fields '
        '(missing invoice number, vendor, and total). Verify the file is a valid vendor invoice.')
    await routes._persist_run(result, 'blank.pdf', ProcessRequest(file_name='blank.pdf'), None)
    stored = await edge_db.query('SELECT invoice_number, vendor_name, decision FROM invoice_runs WHERE run_id = ?', [result.run_id])
    assert stored == [{'invoice_number': 'blank.pdf', 'vendor_name': 'Unknown vendor', 'decision': 'MANUAL_REVIEW'}]


def test_non_positive_total_and_zero_po_price_are_safe(edge_db):
    edge_db.db.execute("UPDATE po_line_items SET unit_price = 0 WHERE po_id = 'po-global'")
    edge_db.db.commit()
    zero = invoice_fields('INV-ZERO', 'Global Supplies', 'PO-9912', 1, 0, 0, 0, 0)
    result = run_engine(edge_db, zero)
    checks = {check.name: check for check in result.decision.validation_checks}
    assert result.decision.decision == 'MANUAL_REVIEW'
    assert checks['non_positive_total'].status == 'fail'
    assert checks['po_line_1'].status in ('pass', 'fail')
    negative = invoice_fields('INV-CREDIT', 'Acme Corp', None, 1, 10, 10, 0, -10)
    credit_result = run_engine(edge_db, negative)
    assert credit_result.decision.decision == 'MANUAL_REVIEW'
    assert any(check.name == 'non_positive_total' for check in credit_result.decision.validation_checks)


def test_currency_mismatch_routes_to_review_without_line_comparison(edge_db):
    fields = invoice_fields('INV-INR', 'Acme Corp', 'PO-8820', 1, 100, 100, 0, 100, currency='INR')
    result = run_engine(edge_db, fields)
    checks = {check.name: check for check in result.decision.validation_checks}
    assert result.decision.decision == 'MANUAL_REVIEW'
    assert checks['currency_mismatch'].status == 'fail'
    assert 'po_line_1' not in checks


def test_zero_and_multiple_line_invoices_reconcile_without_index_errors(edge_db):
    no_lines = invoice_fields('INV-NO-LINES', 'Acme Corp', 'PO-8820', 1, 2000, 2000, 0, 2000)
    no_lines.line_items = []
    result = run_engine(edge_db, no_lines)
    assert result.decision is not None
    assert not any(check.name.startswith('po_line_') for check in result.decision.validation_checks)

    fields = invoice_fields('INV-MULTI', 'Acme Corp', 'PO-8820', 1, 2000, 2030, 0, 2030)
    fields.line_items.extend([
        ef({'description': 'Travel', 'quantity': 1, 'unit_price': 10, 'amount': 10}),
        ef({'description': 'Consulting', 'quantity': 1, 'unit_price': 20, 'amount': 20}),
    ])
    multi = run_engine(edge_db, fields)
    reconciled = [check for check in multi.decision.validation_checks if check.name.startswith('po_line_')]
    assert len(reconciled) == 3
    assert [check.status for check in reconciled] == ['pass', 'fail', 'fail']


@pytest.mark.asyncio
async def test_pdf_extraction_is_limited_to_two_pages_and_dates_are_tolerant(monkeypatch):
    writer = PdfWriter()
    for _ in range(3): writer.add_blank_page(width=612, height=792)
    source = io.BytesIO(); writer.write(source)
    extractor = InvoiceExtractor(Settings(_env_file=None))
    received = {}
    async def fake_ai(_text, images):
        received['images'] = images
        return LLMExtractedInvoice(), 'mock', 'cf_workers_ai_vision'
    monkeypatch.setattr(extractor, '_ai_extract', fake_ai)
    *_, notes = await extractor.extract(source.getvalue(), 'many-pages.pdf')
    assert len(received['images']) == 2
    assert 'Processed first 2 of 3 pages' in notes
    assert _normalize_date('15 March 2026') == '2026-03-15'
    assert _normalize_date('not a date') is None


@pytest.mark.asyncio
async def test_duplicate_review_is_conflict_and_does_not_credit_twice(edge_db, monkeypatch):
    monkeypatch.setattr(routes, 'd1', edge_db)
    fields = invoice_fields('INV-REVIEW-ONCE', 'Global Supplies', 'PO-9912', 10, 13, 130, 10.73, 140.73)
    result = await InvoiceProcessingEngine(d1=edge_db, extractor=FakeExtractor(fields)).process(b'', 'review.pdf')
    await routes._persist_run(result, 'review.pdf', ProcessRequest(file_name='review.pdf'), None)
    request = ReviewRequest(action='APPROVE', reviewer_name='AP Manager', reviewer_note='Reviewed pricing')
    with pytest.raises(HTTPException) as blank:
        await routes.review_run(result.run_id, ReviewRequest(action='REJECT', reviewer_name='AP Manager', reviewer_note='   '))
    assert blank.value.status_code == 400
    batch = routes._batch
    async def race_lost(_statements): return [{'changes': 0}]
    monkeypatch.setattr(routes, '_batch', race_lost)
    with pytest.raises(HTTPException) as race:
        await routes.review_run(result.run_id, request)
    assert race.value.status_code == 409
    monkeypatch.setattr(routes, '_batch', batch)
    await routes.review_run(result.run_id, request)
    credited = (await edge_db.query('SELECT amount_invoiced FROM purchase_orders WHERE po_number = ?', ['PO-9912']))[0]['amount_invoiced']
    with pytest.raises(HTTPException) as error:
        await routes.review_run(result.run_id, request)
    assert error.value.status_code == 409
    after = (await edge_db.query('SELECT amount_invoiced FROM purchase_orders WHERE po_number = ?', ['PO-9912']))[0]['amount_invoiced']
    assert after == credited


@pytest.mark.asyncio
async def test_workers_ai_429_and_malformed_json_are_bounded_and_secret_safe(monkeypatch):
    settings = Settings(_env_file=None, cloudflare_account_id='test-account', cloudflare_ai_api_token='secret-token')
    extractor = InvoiceExtractor(settings)
    requests = []

    class Client:
        def __init__(self, **_): self.calls = 0
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
        async def post(self, url, **kwargs):
            requests.append(kwargs)
            self.calls += 1
            req = httpx.Request('POST', url)
            if self.calls == 1:
                return httpx.Response(429, request=req)
            return httpx.Response(200, json={'choices': [{'message': {'content': '{bad json'}}]}, request=req)

    monkeypatch.setattr('app.engine.extractor.httpx.AsyncClient', Client)
    with pytest.raises(RuntimeError) as error:
        await extractor._call_ai('test-model', 'invoice')
    assert 'secret-token' not in str(error.value)
    assert len(requests) == 2

    calls = []
    async def fallback(model, _text, _images=None):
        calls.append(model)
        if model == 'primary': raise RuntimeError('Workers AI request failed')
        return LLMExtractedInvoice(invoice_number='INV-1', vendor_name='Acme', total=10,
            line_items=[{'description': 'Service', 'quantity': 1, 'unit_price': 10, 'amount': 10}])
    extractor.settings.cf_ai_vision_model = 'primary'
    extractor.settings.cf_ai_fallback_vision_model = 'fallback'
    monkeypatch.setattr(extractor, '_call_ai', fallback)
    _, model, _ = await extractor._ai_extract('invoice', ['data:image/png;base64,AA=='])
    assert calls == ['primary', 'fallback'] and model == 'fallback'


@pytest.mark.asyncio
async def test_sse_midstream_exception_emits_safe_terminal_error(monkeypatch):
    async def no_op(_): return None
    async def explode(*_args, **_kwargs): raise RuntimeError('database token or traceback must stay private')
    monkeypatch.setattr(routes, '_prepare_repeatable_demo', no_op)
    monkeypatch.setattr(routes, '_process', explode)
    response = await routes.process_stream(file=None, scenario='happy_path')
    frames = [str(frame) async for frame in response.body_iterator]
    body = ''.join(frames)
    assert 'event: error' in body and 'Invoice processing failed. Please try again.' in body
    assert 'database token' not in body and 'Traceback' not in body
