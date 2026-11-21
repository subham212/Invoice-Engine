from __future__ import annotations

import inspect
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.models.models import (
    AuditTrace, DecisionResult, DecisionStatus, DuplicateEvidence, ExtractionMetadata,
    ExtractedInvoiceFields, InvoiceStatus, LLMExtractedInvoice, ProcessResponse,
    PurchaseOrder, StageResult, ValidationCheck, ValidationStatus, Vendor,
    VendorStatus, WorkflowStage,
)
from .decision_arbiter import DecisionArbiter
from .duplicate_detector import detect_duplicates
from .extractor import InvoiceExtractor
from .po_matcher import PurchaseOrderMatcher
from .tolerance_rules import ToleranceRules
from .vendor_guard import match_vendor


def _value(fields: ExtractedInvoiceFields, key: str):
    field = getattr(fields, key, None)
    return field.value if field else None


def _as_extracted_record(fields: ExtractedInvoiceFields, notes: list[str]) -> LLMExtractedInvoice:
    confidence = {key: float(getattr(fields, key).confidence) / 100 for key in fields.model_fields_set if getattr(fields, key, None)}
    return LLMExtractedInvoice(
        invoice_number=_value(fields, 'invoice_number'), vendor_name=_value(fields, 'vendor_name'),
        vendor_tax_id=_value(fields, 'vendor_tax_id'), invoice_date=_value(fields, 'invoice_date'),
        due_date=_value(fields, 'due_date'), po_number=_value(fields, 'purchase_order_number'),
        currency=str(_value(fields, 'currency') or 'USD'), subtotal=_value(fields, 'subtotal'),
        tax_rate_percent=_value(fields, 'tax_rate_percent'), tax_amount=_value(fields, 'tax'),
        total=_value(fields, 'total'),
        line_items=[{'description': item.value.get('description', ''), 'quantity': item.value.get('quantity'),
                     'unit_price': item.value.get('unit_price'), 'amount': item.value.get('amount')} for item in fields.line_items],
        field_confidences=confidence, extraction_notes=notes,
    )


class ProcessingContext:
    def __init__(self, d1=None, storage=None):
        self.d1, self.storage = d1, storage

    async def query(self, sql, params=None):
        return await self.d1.query(sql, params) if self.d1 else []


class InvoiceProcessingEngine:
    def __init__(self, d1=None, storage=None, extractor=None):
        self.context = ProcessingContext(d1, storage)
        self.extractor = extractor or InvoiceExtractor()

    async def _rows(self, sql, params=()):
        return await self.context.query(sql, list(params))

    async def process(self, source, file_name=None, run_id=None, on_stage=None) -> ProcessResponse:
        run_id = run_id or str(uuid4())
        trace = AuditTrace(trace_id=str(uuid4()), run_id=run_id)

        async def stage(name, output):
            now = datetime.now(timezone.utc)
            result = StageResult(stage=name, status='complete', output=output, started_at=now, completed_at=now)
            trace.stages.append(result)
            trace.events.append({'stage': name.value, 'status': 'complete'})
            if on_stage:
                event = on_stage(result)
                if inspect.isawaitable(event):
                    await event

        fields, _, _, metadata, ai_data, notes = await self.extractor.extract(source, file_name)
        extracted = ai_data or _as_extracted_record(fields, notes)
        await stage(WorkflowStage.EXTRACTION, {'fields': fields.model_dump(mode='json'), 'metadata': metadata.model_dump(mode='json'), 'notes': notes})
        try:
            invoice = self.extractor.normalize(fields, file_name)
            await stage(WorkflowStage.NORMALIZATION, {'invoice_number': invoice.invoice_number})
        except ValueError as exc:
            invoice = None
            await stage(WorkflowStage.NORMALIZATION, {'error': str(exc)})

        missing = [key for key in ('invoice_number', 'vendor_name', 'total') if _value(fields, key) in (None, '')]
        if invoice is None:
            checks = [ValidationCheck(name=f'missing_{key}', status=ValidationStatus.FAIL,
                                     message=f'Required field {key.replace("_", " ")} was not found.') for key in missing]
            confidence = 0
            vendor = None
            duplicate = []
            po = None
            reconciliations = []
            po_message = 'Purchase order is missing'
            if not checks:
                checks.append(ValidationCheck(name='invoice_normalization', status=ValidationStatus.FAIL,
                                              message='Required invoice fields could not be normalized.'))
            await stage(WorkflowStage.VALIDATION, {'vendor': 'not checked'})
            await stage(WorkflowStage.MATCHING, {'purchase_order': 'not checked'})
            await stage(WorkflowStage.RECONCILIATION, {'lines': []})
            total = Decimal(str(_value(fields, 'total') or 0))
        else:
            checks = ToleranceRules().check(invoice)
            vendors = [Vendor.model_validate(row) for row in await self._rows('SELECT * FROM vendors')]
            vendor, confidence, vendor_message = match_vendor(invoice.vendor_name, vendors, invoice.vendor_tax_id)
            vendor_status = vendor.status if vendor else None
            if vendor_status == VendorStatus.BLOCKED or vendor_status == VendorStatus.BLOCKED.value:
                vendor_status_result = ValidationStatus.FAIL
            elif vendor and vendor_status in (VendorStatus.ACTIVE, VendorStatus.ACTIVE.value):
                vendor_status_result = ValidationStatus.PASS
            else:
                vendor_status_result = ValidationStatus.FAIL
            checks.append(ValidationCheck(name='vendor_match', status=vendor_status_result, message=vendor_message, actual=confidence))
            await stage(WorkflowStage.VALIDATION, {'vendor': vendor_message, 'vendor_id': vendor.id if vendor else None,
                                                   'vendor_status': vendor.status if vendor else None, 'confidence': confidence})

            if _value(fields, 'total') in (None, ''):
                checks.append(ValidationCheck(name='missing_total', status=ValidationStatus.FAIL, message='Printed invoice total was not found.'))
            for key in ('invoice_number', 'vendor_name', 'total'):
                item = getattr(fields, key, None)
                if item and Decimal(str(item.confidence)) < 70:
                    checks.append(ValidationCheck(name=f'low_confidence_{key}', status=ValidationStatus.FAIL,
                                                  message=f'{key.replace("_", " ").title()} extraction confidence is {item.confidence}%; manager review is required.',
                                                  actual=item.confidence))

            history = await self._rows('SELECT id, run_id, invoice_number, vendor_id_matched, total_amount FROM invoice_runs')
            duplicate = detect_duplicates(invoice, history, vendor.id if vendor else None)
            if duplicate:
                checks.append(ValidationCheck(name='duplicate_check', status=ValidationStatus.FAIL,
                                              message='A prior invoice run matches this invoice.'))

            po = None
            reconciliations = []
            po_message = 'Purchase order is missing'
            if invoice.purchase_order_number:
                po_rows = await self._rows('SELECT * FROM purchase_orders WHERE po_number = ?', (invoice.purchase_order_number,))
                if po_rows:
                    row = po_rows[0]
                    line_rows = await self._rows('SELECT * FROM po_line_items WHERE po_id = ?', (row['id'],))
                    candidate = PurchaseOrder.model_validate({**row, 'line_items': line_rows})
                    if not vendor:
                        po_message = 'Vendor could not be verified against the purchase order'
                    elif candidate.vendor_id != vendor.id:
                        po = candidate
                        po_message = 'Purchase order belongs to a different vendor'
                    else:
                        po = candidate
                        po, reconciliations, po_message = PurchaseOrderMatcher().resolve(invoice, [po], vendor.id if vendor else None)
                else:
                    po_message = 'Purchase order number was provided but not found'
            if po_message == 'Purchase order is missing':
                checks.append(ValidationCheck(name='po_match', status=ValidationStatus.WARN, message=po_message))
            elif po is None or 'different vendor' in po_message.lower() or 'not found' in po_message.lower():
                checks.append(ValidationCheck(name='po_match', status=ValidationStatus.FAIL, message=po_message))
            else:
                checks.append(ValidationCheck(name='po_match', status=ValidationStatus.PASS, message=po_message))

            if po and (po.status in {'closed', 'cancelled'} or po.amount_invoiced >= po.total_amount):
                checks.append(ValidationCheck(name='po_hard_cap', status=ValidationStatus.FAIL,
                                              message=f'Purchase order {po.po_number} is {po.status}; do not bill against it.'))
            elif po:
                remaining = max(Decimal('0'), po.total_amount - po.amount_invoiced)
                if invoice.totals.subtotal > remaining:
                    amount = lambda value: f'${value:,.2f}' if invoice.totals.currency == 'USD' else f'{invoice.totals.currency} {value:,.2f}'
                    checks.append(ValidationCheck(name='po_hard_cap', status=ValidationStatus.FAIL,
                                                  message=f'Invoice subtotal {amount(invoice.totals.subtotal)} exceeds remaining PO balance {amount(remaining)}.'))
            if reconciliations:
                for index, item in enumerate(reconciliations, 1):
                    checks.append(ValidationCheck(name=f'po_line_{index}', status=item.status,
                                                  message=item.explanation or f'Line {index} reconciliation',
                                                  expected=item.unit_price_ordered, actual=item.unit_price_invoiced))
                if any(item.quantity_invoiced < item.quantity_ordered for item in reconciliations):
                    remaining_after = max(Decimal('0'), po.total_amount - po.amount_invoiced - invoice.totals.subtotal)
                    currency = invoice.totals.currency
                    checks.append(ValidationCheck(name='partial_billing', status=ValidationStatus.WARN,
                                                  message=f'Invoice bills {currency} {invoice.totals.subtotal:,.2f} against a {currency} {po.total_amount:,.2f} PO; {currency} {remaining_after:,.2f} remains after this invoice.',
                                                  expected=po.total_amount, actual=invoice.totals.subtotal))
            elif po and not po_message.startswith('Purchase order is exhausted'):
                checks.append(ValidationCheck(name='line_reconciliation', status=ValidationStatus.WARN,
                                              message='No invoice lines could be reconciled to the purchase order.'))
            await stage(WorkflowStage.MATCHING, {'purchase_order': po.po_number if po and 'different vendor' not in po_message.lower() else None,
                                                 'message': po_message})
            await stage(WorkflowStage.RECONCILIATION, {'lines': [item.model_dump(mode='json') for item in reconciliations],
                                                       'purchase_order': po.po_number if po else None})
            total = invoice.totals.total

        status_value = vendor.status if invoice and vendor else None
        decision = DecisionArbiter().decide(checks, duplicate, confidence, po_message, total, status_value, missing)
        await stage(WorkflowStage.DECISION, {'decision': decision.decision, 'reason': decision.reason,
                                             'executive_summary': decision.executive_summary})
        return ProcessResponse(run_id=run_id, status=InvoiceStatus.COMPLETE if invoice else InvoiceStatus.FAILED,
                               invoice=invoice, decision=decision, trace=trace,
                               extraction_metadata=metadata, extracted_fields=extracted)
