from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse

from app.core.config import get_settings
from app.core.d1_client import D1Client
from app.core.supabase_storage import SupabaseStorageClient
from app.data.scenarios import scenario_metadata
from app.engine.pipeline import InvoiceProcessingEngine
from app.models.models import DashboardStats, InvoiceRunResponse, ProcessRequest, ProcessResponse, PurchaseOrder, ReviewRequest, Vendor

router = APIRouter(prefix='/api', tags=['invoice-processing'])
logger = logging.getLogger(__name__)
settings = get_settings()
d1 = D1Client(settings)
storage = SupabaseStorageClient(settings)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.webp'}
ALLOWED_TYPES = {'application/pdf', 'image/png', 'image/jpeg', 'image/webp', 'application/octet-stream'}


def _service_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail='The requested backend service is unavailable')


async def _query(sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    try:
        return await d1.query(sql, params)
    except Exception as exc:
        raise _service_error(exc) from exc


async def _batch(statements: list[tuple[str, list[Any] | None]]) -> list[dict[str, Any]]:
    try:
        return await d1.batch(statements)
    except Exception as exc:
        raise _service_error(exc) from exc


def _parse_json(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return default


def _effective_decision(row: dict[str, Any]) -> str:
    human = row.get('human_decision')
    if human == 'APPROVED_OVERRIDE':
        return 'APPROVED'
    if human == 'REJECTED_OVERRIDE':
        return 'REJECTED'
    return str(row.get('decision') or 'MANUAL_REVIEW')


def _run_response(row: dict[str, Any]) -> InvoiceRunResponse:
    trace = _parse_json(row.get('trace_json'), None)
    if trace and 'trace_id' not in trace:
        trace = None
    invoice = _parse_json(row.get('invoice_json'), None)
    extracted = trace.get('extracted_fields') if trace else None
    metadata = {
        'extraction_method': row.get('extraction_method') or 'pdfplumber_fallback',
        'model_used': row.get('model_used'),
        'extraction_latency_ms': row.get('extraction_latency_ms') or 0,
        'is_scanned_document': bool(row.get('is_scanned')),
    }
    human_decision = row.get('human_decision')
    human_review = (trace or {}).get('human_review', {})
    reason = human_review.get('reviewer_note') if human_decision else row.get('decision_reason')
    decision = {
        'decision': _effective_decision(row),
        'reason': reason or row.get('decision_reason'),
        'executive_summary': (human_review.get('executive_summary') or '') if human_decision else (row.get('executive_summary') or ''),
    }
    return InvoiceRunResponse(
        id=row.get('id'), run_id=row['run_id'], status='complete', scenario_key=row.get('scenario_key'),
        invoice=invoice, decision=decision, trace=trace, extracted_fields=extracted,
        extraction_metadata=metadata, created_at=row.get('created_at'), human_decision=human_decision,
        reviewer_name=row.get('reviewer_name'), reviewer_note=row.get('reviewer_note'), reviewed_at=row.get('reviewed_at'),
        display_id=row.get('invoice_number') or row['run_id'], vendor_display=row.get('vendor_name') or 'Unknown vendor',
        total_amount=row.get('total_amount') or 0,
    )


def _stage_output(result: ProcessResponse, name: str) -> dict[str, Any]:
    for stage in result.trace.stages if result.trace else []:
        if stage.stage == name or getattr(stage.stage, 'value', None) == name:
            return stage.output
    return {}


async def _persist_run(result: ProcessResponse, file_name: str, request: ProcessRequest | None, source_key: str | None) -> None:
    if not result.decision:
        return
    extracted = result.extracted_fields
    invoice = result.invoice
    invoice_number = (invoice.invoice_number if invoice else None) or (extracted.invoice_number if extracted else None) or Path(file_name).name or result.run_id
    vendor_name = (invoice.vendor_name if invoice else None) or (extracted.vendor_name if extracted else None) or 'Unknown vendor'
    total = invoice.totals.total if invoice else (extracted.total if extracted and extracted.total is not None else 0)
    subtotal = invoice.totals.subtotal if invoice else (extracted.subtotal if extracted and extracted.subtotal is not None else 0)
    currency = invoice.totals.currency if invoice else (extracted.currency if extracted else 'USD')
    vendor_output = _stage_output(result, 'validation')
    po_output = _stage_output(result, 'matching')
    vendor_id = vendor_output.get('vendor_id')
    po_number = po_output.get('purchase_order')
    decision = str(result.decision.decision)
    credit_amount = float(subtotal) if po_number else 0.0
    trace = result.trace.model_dump(mode='json') if result.trace else {'trace_id': result.run_id}
    trace['extracted_fields'] = extracted.model_dump(mode='json') if extracted else None
    statements = [
        ("INSERT INTO invoice_runs (id, run_id, scenario_key, invoice_number, vendor_name, vendor_id_matched, po_number_matched, total_amount, decision, decision_reason, trace_json, source_object_key, currency, extraction_method, model_used, extraction_latency_ms, is_scanned, executive_summary, invoice_json, credit_amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
         [result.run_id, result.run_id, request.scenario.key if request and request.scenario else None,
          str(invoice_number), str(vendor_name), vendor_id, po_number, float(total), decision, result.decision.reason,
          json.dumps(trace), source_key, currency,
          result.extraction_metadata.extraction_method if result.extraction_metadata else 'pdfplumber_fallback',
          result.extraction_metadata.model_used if result.extraction_metadata else None,
          result.extraction_metadata.extraction_latency_ms if result.extraction_metadata else 0,
          int(result.extraction_metadata.is_scanned_document) if result.extraction_metadata else 0,
          result.decision.executive_summary,
          json.dumps(invoice.model_dump(mode='json')) if invoice else None, credit_amount]),
        ("UPDATE purchase_orders SET amount_invoiced = amount_invoiced + ?, status = CASE WHEN amount_invoiced + ? >= total_amount THEN 'closed' ELSE 'partially_utilized' END WHERE po_number = ? AND ? > 0 AND amount_invoiced + ? <= total_amount AND status IN ('open','partially_utilized') AND EXISTS (SELECT 1 FROM invoice_runs WHERE run_id = ? AND po_credited = 0 AND decision IN ('APPROVED','APPROVED_WITH_WARNING'))",
         [credit_amount, credit_amount, po_number or '', credit_amount, credit_amount, result.run_id]),
        ('UPDATE invoice_runs SET po_credited = 1 WHERE run_id = ? AND po_number_matched IS NOT NULL AND credit_amount > 0 AND changes() > 0', [result.run_id]),
    ]
    await _batch(statements)


async def _process(source: bytes, file_name: str, request: ProcessRequest | None = None, on_stage: Any = None) -> ProcessResponse:
    run_id = str(uuid4())
    source_key = request.source_object_key.strip() if request and request.source_object_key else None
    if source_key:
        try:
            source = await asyncio.to_thread(storage.download_file, source_key)
        except Exception as exc:
            raise _service_error(exc) from exc
    result = await InvoiceProcessingEngine(d1=d1, storage=storage).process(source, file_name=file_name, run_id=run_id, on_stage=on_stage)
    await _persist_run(result, file_name, request, source_key)
    return result


async def _read_upload(file: UploadFile) -> tuple[bytes, str]:
    filename = Path(file.filename or 'invoice.pdf').name
    if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail='Upload a PDF, PNG, JPG, JPEG, or WEBP invoice.')
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=415, detail='The uploaded file type is not supported.')
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail='Invoice uploads are limited to 10 MB.')
    if not data:
        raise HTTPException(status_code=400, detail='Uploaded file is empty.')
    return data, filename


@router.get('/scenarios/{scenario_key}/pdf')
async def scenario_pdf(scenario_key: str) -> FileResponse:
    if scenario_key not in {item['key'] for item in scenario_metadata()}:
        raise HTTPException(status_code=404, detail='Scenario not found')
    sample = Path(__file__).resolve().parents[2] / 'generated_samples' / f'{scenario_key}.pdf'
    if not sample.exists():
        raise HTTPException(status_code=404, detail='Scenario sample is unavailable')
    return FileResponse(sample, media_type='application/pdf', filename=sample.name)


@router.get('/scenarios')
async def scenarios() -> list[dict[str, Any]]:
    sample_dir = Path(__file__).resolve().parents[2] / 'generated_samples'
    available = {path.stem for path in sample_dir.glob('*.pdf')} if sample_dir.exists() else set()
    return [scenario for scenario in scenario_metadata() if scenario['key'] in available]


@router.get('/history', response_model=list[InvoiceRunResponse])
async def history(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), decision: str | None = None) -> list[InvoiceRunResponse]:
    sql = 'SELECT * FROM invoice_runs'
    params: list[Any] = []
    if decision:
        sql += " WHERE CASE human_decision WHEN 'APPROVED_OVERRIDE' THEN 'APPROVED' WHEN 'REJECTED_OVERRIDE' THEN 'REJECTED' ELSE decision END = ?"
        params.append(decision)
    sql += ' ORDER BY created_at DESC LIMIT ? OFFSET ?'
    params.extend([limit, offset])
    return [_run_response(row) for row in await _query(sql, params)]


@router.get('/history/{run_id}', response_model=InvoiceRunResponse)
async def history_detail(run_id: str) -> InvoiceRunResponse:
    rows = await _query('SELECT * FROM invoice_runs WHERE run_id = ?', [run_id])
    if not rows:
        raise HTTPException(status_code=404, detail='Invoice run not found')
    return _run_response(rows[0])


@router.post('/run', response_model=ProcessResponse, status_code=status.HTTP_200_OK)
async def run(request: ProcessRequest) -> ProcessResponse:
    if request.source_object_key is None:
        raise HTTPException(status_code=422, detail='source_object_key is required for JSON processing requests')
    return await _process(b'', request.file_name, request)


@router.post('/upload', response_model=ProcessResponse, status_code=status.HTTP_201_CREATED)
async def upload(file: UploadFile = File(...), scenario: str | None = Form(None)) -> ProcessResponse:
    data, filename = await _read_upload(file)
    request = ProcessRequest(file_name=filename, scenario={'key': scenario} if scenario else None)
    return await _process(data, filename, request)


@router.post('/process', status_code=200)
async def process_stream(file: UploadFile | None = File(None), scenario: str | None = Form(None)) -> StreamingResponse:
    if file:
        data, file_name = await _read_upload(file)
    elif scenario and scenario in {item['key'] for item in scenario_metadata()}:
        sample = Path(__file__).resolve().parents[2] / 'generated_samples' / f'{scenario}.pdf'
        if not sample.exists():
            raise HTTPException(status_code=404, detail='Scenario sample is unavailable')
        data, file_name = sample.read_bytes(), sample.name
    else:
        raise HTTPException(status_code=400, detail='A supported invoice upload or configured scenario is required')
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def emit(result: Any) -> None:
        names = {'extraction': 'extraction', 'normalization': 'extraction', 'validation': 'vendor_guard',
                 'matching': 'po_matching', 'reconciliation': 'financial_validation', 'decision': 'decision'}
        raw_name = result.stage.value if hasattr(result.stage, 'value') else str(result.stage)
        stage = names.get(raw_name, raw_name)
        await queue.put(f'event: stage\ndata: {json.dumps({"stage": stage, "status": "passed", "progress": 100, "message": f"{stage.replace("_", " ").title()} complete"})}\n\n')

    async def execute() -> None:
        try:
            await queue.put('event: stage\ndata: {"stage":"intake","status":"passed","progress":100,"message":"Invoice received"}\n\n')
            result = await _process(data, file_name, ProcessRequest(file_name=file_name, scenario={'key': scenario} if scenario else None), on_stage=emit)
            await queue.put(f'event: complete\ndata: {json.dumps(result.model_dump(mode="json"))}\n\n')
        except HTTPException as exc:
            await queue.put(f'event: error\ndata: {json.dumps({"detail": exc.detail})}\n\n')
        except Exception:
            logger.exception('Invoice processing failed: file_name=%s scenario=%s', file_name, scenario)
            await queue.put(f'event: error\ndata: {json.dumps({"detail": "Invoice processing failed"})}\n\n')
        finally:
            await queue.put(None)

    async def events():
        task = asyncio.create_task(execute())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=10)
                except asyncio.TimeoutError:
                    yield ': keep-alive\n\n'
                    continue
                if item is None:
                    break
                yield item
        finally:
            if not task.done():
                task.cancel()
    return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@router.get('/purchase-orders', response_model=list[PurchaseOrder])
async def purchase_orders(vendor_id: str | None = None, po_status: str | None = Query(None, alias='status')) -> list[PurchaseOrder]:
    sql = 'SELECT * FROM purchase_orders WHERE 1=1'
    params: list[Any] = []
    if vendor_id:
        sql += ' AND vendor_id = ?'; params.append(vendor_id)
    if po_status:
        sql += ' AND status = ?'; params.append(po_status)
    rows = await _query(sql, params)
    result = []
    for row in rows:
        lines = await _query('SELECT * FROM po_line_items WHERE po_id = ?', [row['id']])
        result.append(PurchaseOrder.model_validate({**row, 'line_items': lines}))
    return result


@router.get('/vendors', response_model=list[Vendor])
async def vendors(vendor_status: str | None = Query(None, alias='status')) -> list[Vendor]:
    sql = 'SELECT * FROM vendors' + (' WHERE status = ?' if vendor_status else '')
    rows = await _query(sql, [vendor_status] if vendor_status else None)
    return [Vendor.model_validate(row) for row in rows]


@router.get('/stats', response_model=DashboardStats)
@router.get('/dashboard', response_model=DashboardStats)
async def stats() -> DashboardStats:
    rows = await _query("SELECT CASE human_decision WHEN 'APPROVED_OVERRIDE' THEN 'APPROVED' WHEN 'REJECTED_OVERRIDE' THEN 'REJECTED' ELSE decision END AS decision, currency, COUNT(*) AS count, COALESCE(SUM(total_amount), 0) AS amount FROM invoice_runs GROUP BY decision, currency")
    counts: dict[str, int] = {}
    totals: dict[str, Decimal] = {}
    for row in rows:
        counts[row['decision']] = counts.get(row['decision'], 0) + int(row['count'])
        currency = row.get('currency') or 'USD'
        totals[currency] = totals.get(currency, Decimal('0')) + Decimal(str(row['amount'] or 0))
    total_by_currency = {currency: amount.quantize(Decimal('0.01')) for currency, amount in totals.items()}
    total_amount = sum(total_by_currency.values(), Decimal('0.00'))
    return DashboardStats(total_runs=sum(counts.values()), approved=counts.get('APPROVED', 0),
                          approved_with_warning=counts.get('APPROVED_WITH_WARNING', 0), manual_review=counts.get('MANUAL_REVIEW', 0),
                          rejected=counts.get('REJECTED', 0), total_amount=total_amount, total_by_currency=total_by_currency,
                          by_status=counts, as_of=datetime.now(timezone.utc))


@router.get('/audit/{run_id}')
async def audit(run_id: str) -> dict[str, Any]:
    rows = await _query('SELECT trace_json FROM invoice_runs WHERE run_id = ?', [run_id])
    if not rows:
        raise HTTPException(status_code=404, detail='Invoice run not found')
    return _parse_json(rows[0].get('trace_json'), {})


@router.post('/runs/{run_id}/review', response_model=InvoiceRunResponse)
async def review_run(run_id: str, review: ReviewRequest) -> InvoiceRunResponse:
    rows = await _query('SELECT * FROM invoice_runs WHERE run_id = ?', [run_id])
    if not rows:
        raise HTTPException(status_code=404, detail='Invoice run not found')
    row = rows[0]
    if row.get('human_decision'):
        raise HTTPException(status_code=409, detail='This invoice run has already been reviewed.')
    if row.get('decision') != 'MANUAL_REVIEW':
        raise HTTPException(status_code=409, detail='Only invoices awaiting manual review can be overridden.')
    human_decision = 'APPROVED_OVERRIDE' if review.action == 'APPROVE' else 'REJECTED_OVERRIDE'
    reviewed_at = datetime.now(timezone.utc).isoformat()
    trace = _parse_json(row.get('trace_json'), {})
    trace['human_review'] = {'action': review.action, 'human_decision': human_decision, 'reviewer_name': review.reviewer_name,
                             'reviewer_note': review.reviewer_note, 'reviewed_at': reviewed_at,
                             'executive_summary': f'{review.reviewer_name} {"approved" if review.action == "APPROVE" else "rejected"} this invoice. {review.reviewer_note}'}
    if review.action == 'APPROVE' and row.get('po_number_matched') and float(row.get('credit_amount') or 0) > 0:
        po_rows = await _query('SELECT total_amount, amount_invoiced, status FROM purchase_orders WHERE po_number = ?', [row['po_number_matched']])
        if not po_rows or float(po_rows[0]['amount_invoiced']) + float(row['credit_amount']) > float(po_rows[0]['total_amount']):
            raise HTTPException(status_code=409, detail='The purchase order has no remaining balance for this override.')
    statements = [
        ('UPDATE invoice_runs SET human_decision = ?, reviewer_name = ?, reviewer_note = ?, reviewed_at = ?, trace_json = ? WHERE run_id = ? AND human_decision IS NULL',
         [human_decision, review.reviewer_name, review.reviewer_note, reviewed_at, json.dumps(trace), run_id]),
        ("UPDATE purchase_orders SET amount_invoiced = amount_invoiced + ?, status = CASE WHEN amount_invoiced + ? >= total_amount THEN 'closed' ELSE 'partially_utilized' END WHERE po_number = (SELECT po_number_matched FROM invoice_runs WHERE run_id = ?) AND ? = 'APPROVED_OVERRIDE' AND ? > 0 AND amount_invoiced + ? <= total_amount AND status IN ('open','partially_utilized') AND EXISTS (SELECT 1 FROM invoice_runs WHERE run_id = ? AND human_decision = 'APPROVED_OVERRIDE' AND po_credited = 0)",
         [float(row.get('credit_amount') or 0), float(row.get('credit_amount') or 0), run_id, human_decision,
          float(row.get('credit_amount') or 0), float(row.get('credit_amount') or 0), run_id]),
        ("UPDATE invoice_runs SET po_credited = 1 WHERE run_id = ? AND po_number_matched IS NOT NULL AND human_decision = 'APPROVED_OVERRIDE' AND po_credited = 0 AND changes() > 0", [run_id]),
    ]
    await _batch(statements)
    updated = await _query('SELECT * FROM invoice_runs WHERE run_id = ?', [run_id])
    return _run_response(updated[0])


@router.post('/reset', status_code=204)
async def reset() -> None:
    await _batch([
        ('DELETE FROM invoice_runs', []),
        ("INSERT OR IGNORE INTO invoice_runs (id, run_id, scenario_key, invoice_number, vendor_name, vendor_id_matched, po_number_matched, total_amount, decision, decision_reason, trace_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
         ['run-novas-historical', 'RUN-HIST-7729', 'duplicate-baseline', 'INV-7729',
          'Novas Industrial Inc.', 'vendor-novas', 'PO-7729', 4500.0, 'APPROVED',
          'Historical payment retained for duplicate detection.', '{"source":"seed","paid":true}', '2026-09-02 10:00:00']),
        ("UPDATE purchase_orders SET amount_invoiced = base_amount_invoiced, status = CASE WHEN base_amount_invoiced >= total_amount THEN 'closed' WHEN base_amount_invoiced > 0 THEN 'partially_utilized' ELSE 'open' END", []),
    ])
