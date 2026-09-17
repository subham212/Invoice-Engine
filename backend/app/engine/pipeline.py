from __future__ import annotations
import inspect
from datetime import datetime, timezone
from uuid import uuid4
from app.models.models import *
from .extractor import InvoiceExtractor
from .vendor_guard import match_vendor
from .duplicate_detector import detect_duplicates
from .po_matcher import PurchaseOrderMatcher
from .decision_arbiter import DecisionArbiter

class ProcessingContext:
    def __init__(self, d1=None, storage=None): self.d1, self.storage = d1, storage
    async def query(self, sql, params=None): return await self.d1.query(sql, params) if self.d1 else []

class InvoiceProcessingEngine:
    def __init__(self, d1=None, storage=None, extractor=None): self.context=ProcessingContext(d1,storage); self.extractor=extractor or InvoiceExtractor()
    async def _rows(self, sql, params=()): return await self.context.query(sql, list(params))
    async def process(self, source, file_name=None, run_id=None, on_stage=None) -> ProcessResponse:
        run_id=run_id or str(uuid4()); trace=AuditTrace(trace_id=str(uuid4()),run_id=run_id)
        async def stage(name, output):
            result=StageResult(stage=name,status='complete',output=output,started_at=datetime.now(timezone.utc),completed_at=datetime.now(timezone.utc)); trace.stages.append(result); trace.events.append({'stage':name.value,'status':'complete'})
            if on_stage:
                event=on_stage(result)
                if inspect.isawaitable(event): await event
        fields,text,_=self.extractor.extract(source); await stage(WorkflowStage.EXTRACTION, {'fields':fields.model_dump()})
        try: invoice=self.extractor.normalize(fields,file_name); await stage(WorkflowStage.NORMALIZATION, {'invoice_number':invoice.invoice_number})
        except ValueError as exc:
            decision=DecisionResult(decision=DecisionStatus.MANUAL_REVIEW,reason=str(exc),confidence=Decimal('0')); return ProcessResponse(run_id=run_id,status=InvoiceStatus.FAILED,decision=decision,trace=trace)
        vendors=[Vendor.model_validate(r) for r in await self._rows('SELECT * FROM vendors')]
        vendor,confidence,vmsg=match_vendor(invoice.vendor_name,vendors,invoice.vendor_tax_id)
        history=await self._rows('SELECT id, run_id, invoice_number, vendor_id_matched, total_amount FROM invoice_runs', ())
        duplicate=detect_duplicates(invoice,history,vendor.id if vendor else None)
        checks=self.extractor and __import__('app.engine.tolerance_rules',fromlist=['ToleranceRules']).ToleranceRules().check(invoice)
        pos=[]
        if vendor:
            po_rows=await self._rows('SELECT * FROM purchase_orders WHERE vendor_id = ? AND status = ?', (vendor.id,'open'))
            for row in po_rows:
                line_rows=await self._rows('SELECT * FROM po_line_items WHERE po_id = ?', (row['id'],))
                pos.append(PurchaseOrder.model_validate({**row, 'line_items': line_rows}))
        po,recs,pomsg=PurchaseOrderMatcher().resolve(invoice,pos,vendor.id if vendor else None)
        checks += [ValidationCheck(name='vendor_match',status=ValidationStatus.PASS if confidence>=70 else ValidationStatus.FAIL,message=vmsg,actual=confidence), ValidationCheck(name='po_match',status=ValidationStatus.PASS if po else ValidationStatus.WARN,message=pomsg)]
        checks += [ValidationCheck(name='line_reconciliation', status=ValidationStatus.PASS if recs and all(r.status == ValidationStatus.PASS for r in recs) else (ValidationStatus.WARN if not recs or not po else ValidationStatus.FAIL), message='PO line quantities and prices reconciled' if recs else 'No PO lines reconciled')]
        if recs and any(r.quantity_invoiced < r.quantity_ordered for r in recs):
            checks.append(ValidationCheck(name='partial_billing', status=ValidationStatus.WARN, message='Invoice covers only part of the purchase order'))
        await stage(WorkflowStage.RECONCILIATION, {'lines': [item.model_dump(mode='json') for item in recs], 'purchase_order': po.po_number if po else None})
        decision=DecisionArbiter().decide(checks,duplicate,confidence,pomsg); await stage(WorkflowStage.DECISION, {'decision':decision.decision, 'reason': decision.reason}); return ProcessResponse(run_id=run_id,status=InvoiceStatus.COMPLETE,invoice=invoice,decision=decision,trace=trace)
