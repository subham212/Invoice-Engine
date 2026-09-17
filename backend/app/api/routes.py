from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
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
from app.models.models import DashboardStats, InvoiceRunResponse, ProcessRequest, ProcessResponse, PurchaseOrder, Vendor

router = APIRouter(prefix='/api', tags=['invoice-processing'])
settings = get_settings()
d1 = D1Client(settings)
storage = SupabaseStorageClient(settings)


def _service_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail='The requested backend service is unavailable')


async def _query(sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    try:
        return await d1.query(sql, params)
    except Exception as exc:
        raise _service_error(exc) from exc


def _parse_json(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return default


def _run_response(row: dict[str, Any]) -> InvoiceRunResponse:
    trace = _parse_json(row.get('trace_json'), None)
    if trace and 'trace_id' not in trace:
        trace = None
    return InvoiceRunResponse(
        id=row.get('id'), run_id=row['run_id'], status='complete', scenario_key=row.get('scenario_key'),
        invoice={'invoice_number': row['invoice_number'], 'vendor_name': row['vendor_name'], 'totals': {'subtotal': row['total_amount'], 'tax': 0, 'total': row['total_amount'], 'currency': 'USD'}},
        decision={'decision': row['decision'], 'reason': row['decision_reason']}, trace=trace,
        created_at=row.get('created_at'),
    )


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
        sql += ' WHERE decision = ?'; params.append(decision)
    sql += ' ORDER BY created_at DESC LIMIT ? OFFSET ?'; params.extend([limit, offset])
    rows = await _query(sql, params)
    return [_run_response(row) for row in rows]


@router.get('/history/{run_id}', response_model=InvoiceRunResponse)
async def history_detail(run_id: str) -> InvoiceRunResponse:
    rows = await _query('SELECT * FROM invoice_runs WHERE run_id = ?', [run_id])
    if not rows: raise HTTPException(status_code=404, detail='Invoice run not found')
    return _run_response(rows[0])


async def _process(source: bytes, file_name: str, request: ProcessRequest | None = None, on_stage: Any = None) -> ProcessResponse:
    run_id = str(uuid4())
    source_key = request.source_object_key.strip() if request and request.source_object_key else None
    if source_key:
        try: source = storage.download_file(source_key)
        except Exception as exc: raise _service_error(exc) from exc
    engine = InvoiceProcessingEngine(d1=d1, storage=storage)
    result = await engine.process(source, file_name=file_name, run_id=run_id, on_stage=on_stage)
    if result.invoice and result.decision:
        trace_json = json.dumps(result.trace.model_dump(mode='json') if result.trace else {})
        try:
            await _query('INSERT INTO invoice_runs (id, run_id, scenario_key, invoice_number, vendor_name, total_amount, decision, decision_reason, trace_json, source_object_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', [run_id, run_id, request.scenario.key if request and request.scenario else None, result.invoice.invoice_number, result.invoice.vendor_name, float(result.invoice.totals.total), result.decision.decision, result.decision.reason, trace_json, source_key])
        except HTTPException:
            pass
    return result


@router.post('/run', response_model=ProcessResponse, status_code=status.HTTP_200_OK)
async def run(request: ProcessRequest) -> ProcessResponse:
    if request.source_object_key is None: raise HTTPException(status_code=422, detail='source_object_key is required for JSON processing requests')
    return await _process(b'', request.file_name, request)


@router.post('/upload', response_model=ProcessResponse, status_code=status.HTTP_201_CREATED)
async def upload(file: UploadFile = File(...), scenario: str | None = Form(None)) -> ProcessResponse:
    if file.content_type not in {'application/pdf', 'application/octet-stream'}: raise HTTPException(status_code=415, detail='Only PDF uploads are supported')
    data = await file.read()
    if not data: raise HTTPException(status_code=400, detail='Uploaded file is empty')
    return await _process(data, file.filename or 'invoice.pdf', ProcessRequest(file_name=file.filename or 'invoice.pdf', scenario={'key': scenario} if scenario else None))


@router.post('/process', status_code=200)
async def process_stream(file: UploadFile | None = File(None), scenario: str | None = Form(None)) -> StreamingResponse:
    if file:
        data = await file.read()
        file_name = file.filename or 'invoice.pdf'
    elif scenario and scenario in {item['key'] for item in scenario_metadata()}:
        sample = Path(__file__).resolve().parents[2] / 'generated_samples' / f'{scenario}.pdf'
        # When running in Docker on Render, files might be in different path
        if not sample.exists():
            sample = Path(__file__).resolve().parents[2] / 'samples' / f'{scenario}.pdf'
            if not sample.exists(): raise HTTPException(status_code=404, detail='Scenario sample is unavailable')
        data, file_name = sample.read_bytes(), sample.name
    else:
        raise HTTPException(status_code=400, detail='A PDF upload or configured scenario is required')
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    async def emit(result: Any) -> None:
        stage_map = {'extraction': 'Extraction', 'normalization': 'Extraction', 'validation': 'Financial Validation', 'matching': 'PO Matching', 'reconciliation': 'Financial Validation', 'decision': 'Decision'}
        stage_name = result.stage.value if hasattr(result.stage, 'value') else str(result.stage)
        stage = stage_map.get(stage_name, stage_name)
        await queue.put(f'event: stage\ndata: {json.dumps({"stage": stage.lower().replace(" ", "_"), "status": "passed", "progress": 100, "message": f"{stage} complete"})}\n\n')
    async def execute() -> None:
        try:
            result = await _process(data, file_name, ProcessRequest(file_name=file_name, scenario={'key': scenario} if scenario else None), on_stage=emit)
            await queue.put(f'event: complete\ndata: {json.dumps(result.model_dump(mode="json"))}\n\n')
        except HTTPException as exc:
            await queue.put(f'event: error\ndata: {json.dumps({"detail": exc.detail})}\n\n')
        except Exception:
            await queue.put(f'event: error\ndata: {json.dumps({"detail": "Invoice processing failed"})}\n\n')
        finally:
            await queue.put(None)
    async def events():
        task = asyncio.create_task(execute())
        try:
            while True:
                item = await queue.get()
                if item is None: break
                yield item
        finally:
            if not task.done(): task.cancel()
    return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@router.get('/purchase-orders', response_model=list[PurchaseOrder])
async def purchase_orders(vendor_id: str | None = None, po_status: str | None = Query(None, alias='status')) -> list[PurchaseOrder]:
    sql = 'SELECT * FROM purchase_orders WHERE 1=1'; params: list[Any] = []
    if vendor_id: sql += ' AND vendor_id = ?'; params.append(vendor_id)
    if po_status: sql += ' AND status = ?'; params.append(po_status)
    rows = await _query(sql, params)
    result = []
    for row in rows:
        lines = await _query('SELECT * FROM po_line_items WHERE po_id = ?', [row['id']])
        result.append(PurchaseOrder.model_validate({**row, 'line_items': lines}))
    return result


@router.get('/vendors', response_model=list[Vendor])
async def vendors(vendor_status: str | None = Query(None, alias='status')) -> list[Vendor]:
    if vendor_status: return [Vendor.model_validate(row) for row in await _query('SELECT * FROM vendors WHERE status = ?', [vendor_status])]
    return [Vendor.model_validate(row) for row in await _query('SELECT * FROM vendors')]


@router.get('/stats', response_model=DashboardStats)
async def stats() -> DashboardStats:
    rows = await _query('SELECT decision, COUNT(*) AS count, COALESCE(SUM(total_amount), 0) AS amount FROM invoice_runs GROUP BY decision')
    counts = {row['decision']: int(row['count']) for row in rows}
    return DashboardStats(total_runs=sum(counts.values()), approved=counts.get('APPROVED', 0), approved_with_warning=counts.get('APPROVED_WITH_WARNING', 0), manual_review=counts.get('MANUAL_REVIEW', 0), rejected=counts.get('REJECTED', 0), total_amount=sum((row['amount'] or 0) for row in rows), by_status=counts, as_of=datetime.now(timezone.utc))


@router.get('/audit/{run_id}')
async def audit(run_id: str) -> dict[str, Any]:
    rows = await _query('SELECT trace_json FROM invoice_runs WHERE run_id = ?', [run_id])
    if not rows: raise HTTPException(status_code=404, detail='Invoice run not found')
    return _parse_json(rows[0].get('trace_json'), {})


@router.post('/reset', status_code=204)
async def reset() -> None:
    try:
        await d1.execute('DELETE FROM invoice_runs')
    except Exception as exc:
        raise _service_error(exc) from exc
