from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
import re
from math import isfinite
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
Quantity = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4)]
Confidence = Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)]


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True, use_enum_values=True)


class VendorStatus(StrEnum):
    ACTIVE = 'active'
    INACTIVE = 'inactive'
    BLOCKED = 'blocked'


class InvoiceStatus(StrEnum):
    RECEIVED = 'received'
    PROCESSING = 'processing'
    COMPLETE = 'complete'
    FAILED = 'failed'


class DecisionStatus(StrEnum):
    APPROVED = 'APPROVED'
    APPROVED_WITH_WARNING = 'APPROVED_WITH_WARNING'
    MANUAL_REVIEW = 'MANUAL_REVIEW'
    REJECTED = 'REJECTED'


class ValidationStatus(StrEnum):
    PASS = 'pass'
    WARN = 'warn'
    FAIL = 'fail'


class WorkflowStage(StrEnum):
    EXTRACTION = 'extraction'
    NORMALIZATION = 'normalization'
    VALIDATION = 'validation'
    MATCHING = 'matching'
    RECONCILIATION = 'reconciliation'
    DECISION = 'decision'


class ExtractedField(Model):
    value: Any | None = None
    confidence: Confidence
    source_text: str | None = None
    page: int | None = Field(default=None, ge=1)
    bounding_box: list[float] | None = Field(default=None, min_length=4, max_length=4)


class ExtractedInvoiceFields(Model):
    invoice_number: ExtractedField | None = None
    invoice_date: ExtractedField | None = None
    due_date: ExtractedField | None = None
    vendor_name: ExtractedField | None = None
    vendor_tax_id: ExtractedField | None = None
    purchase_order_number: ExtractedField | None = None
    currency: ExtractedField | None = None
    subtotal: ExtractedField | None = None
    tax_rate_percent: ExtractedField | None = None
    tax: ExtractedField | None = None
    total: ExtractedField | None = None
    line_items: list[ExtractedField] = Field(default_factory=list)


def _number(value: Any) -> float | None:
    if value is None or value == '':
        return None
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        return number if isfinite(number) else None
    raw = str(value).strip()
    negative = raw.startswith('(') and raw.endswith(')')
    match = re.search(r'-?[\d,]+(?:\.\d+)?', raw.replace('(', '-').replace(')', ''))
    if not match:
        return None
    number = float(match.group().replace(',', ''))
    if not isfinite(number):
        return None
    return -abs(number) if negative else number


class ExtractedLineItem(Model):
    description: str = ''
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = None

    @model_validator(mode='before')
    @classmethod
    def coerce_numbers(cls, values):
        if isinstance(values, dict):
            values = dict(values)
            for key in ('quantity', 'unit_price', 'amount'):
                values[key] = _number(values.get(key))
        return values


class LLMExtractedInvoice(Model):
    invoice_number: str | None = None
    vendor_name: str | None = None
    vendor_tax_id: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    po_number: str | None = None
    currency: str = 'USD'
    subtotal: float | None = None
    tax_rate_percent: float | None = None
    tax_amount: float | None = None
    total: float | None = None
    line_items: list[ExtractedLineItem] = Field(default_factory=list)
    field_confidences: dict[str, Annotated[float, Field(ge=0, le=1)]] = Field(default_factory=dict)
    extraction_notes: list[str] = Field(default_factory=list)

    @field_validator('invoice_number', 'vendor_name', 'vendor_tax_id', 'invoice_date', 'due_date', 'po_number', mode='before')
    @classmethod
    def normalize_optional_text(cls, value):
        if isinstance(value, str) and value.strip().casefold() in {'', 'not provided', 'none', 'n/a', 'na', 'null'}:
            return None
        return value

    @field_validator('extraction_notes', mode='before')
    @classmethod
    def normalize_extraction_notes(cls, value):
        if value is None:
            return []
        return [value] if isinstance(value, str) else value

    @field_validator('currency', mode='before')
    @classmethod
    def normalize_currency(cls, value):
        raw = str(value or 'USD').strip().upper()
        aliases = {'₹': 'INR', 'RS': 'INR', 'RS.': 'INR', '$': 'USD', '€': 'EUR', '£': 'GBP'}
        raw = aliases.get(raw, raw)
        return raw if re.fullmatch(r'[A-Z]{3}', raw) else 'USD'

    @model_validator(mode='before')
    @classmethod
    def coerce_numbers(cls, obj):
        if isinstance(obj, dict):
            obj = dict(obj)
            for key in ('subtotal', 'tax_rate_percent', 'tax_amount', 'total'):
                obj[key] = _number(obj.get(key))
        return obj


class ExtractionMetadata(Model):
    extraction_method: Literal['cf_workers_ai_vision', 'cf_workers_ai_text', 'pdfplumber_fallback', 'vision_fallback']
    model_used: str | None = None
    extraction_latency_ms: int = 0
    is_scanned_document: bool = False


class LineItem(Model):
    item_code: str | None = None
    description: str
    quantity: Quantity
    unit_price: Money
    tax_rate: Annotated[Decimal, Field(ge=0, le=100, max_digits=7, decimal_places=4)] = Decimal('0')
    amount: Annotated[Decimal, Field(max_digits=18, decimal_places=2)] | None = None


class InvoiceTotals(Model):
    subtotal: Annotated[Decimal, Field(max_digits=18, decimal_places=2)]
    tax: Annotated[Decimal, Field(max_digits=18, decimal_places=2)] = Decimal('0')
    total: Annotated[Decimal, Field(max_digits=18, decimal_places=2)]
    currency: str = Field(min_length=3, max_length=3)


class NormalizedInvoice(Model):
    invoice_number: str
    invoice_date: datetime | None = None
    due_date: datetime | None = None
    vendor_name: str
    vendor_tax_id: str | None = None
    purchase_order_number: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    totals: InvoiceTotals
    source_file_name: str | None = None


class Vendor(Model):
    id: str
    legal_name: str
    display_name: str
    tax_id: str
    currency: str = Field(default='USD', min_length=3, max_length=3)
    payment_terms: str = 'NET_30'
    status: VendorStatus = VendorStatus.ACTIVE
    created_at: datetime | None = None


class POLineItem(Model):
    id: str
    po_id: str
    item_code: str | None = None
    description: str
    quantity: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]
    unit_price: Money
    tax_rate: Annotated[Decimal, Field(ge=0, le=100, max_digits=7, decimal_places=4)] = Decimal('0')


class PurchaseOrder(Model):
    id: str
    po_number: str
    vendor_id: str
    total_amount: Money
    amount_invoiced: Money = Decimal('0')
    base_amount_invoiced: Money = Decimal('0')
    currency: str = Field(default='USD', min_length=3, max_length=3)
    status: str = 'open'
    created_at: datetime | None = None
    line_items: list[POLineItem] = Field(default_factory=list)


class WorkflowSubstep(Model):
    name: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class StageResult(Model):
    stage: WorkflowStage
    status: str
    substeps: list[WorkflowSubstep] = Field(default_factory=list)
    output: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ValidationCheck(Model):
    name: str
    status: ValidationStatus
    message: str
    expected: Any | None = None
    actual: Any | None = None


class DuplicateEvidence(Model):
    invoice_run_id: str
    invoice_number: str
    vendor_id: str | None = None
    similarity_score: Confidence | None = None
    matched_fields: list[str] = Field(default_factory=list)
    explanation: str


class LineReconciliation(Model):
    invoice_line_id: str | None = None
    po_line_id: str | None = None
    quantity_invoiced: Quantity
    quantity_ordered: Quantity
    unit_price_invoiced: Money
    unit_price_ordered: Money
    quantity_variance: Decimal
    price_variance: Decimal
    status: ValidationStatus
    explanation: str | None = None


class DecisionResult(Model):
    decision: DecisionStatus
    reason: str
    confidence: Confidence | None = None
    validation_checks: list[ValidationCheck] = Field(default_factory=list)
    duplicate_evidence: list[DuplicateEvidence] = Field(default_factory=list)
    executive_summary: str = ''


class AuditTrace(Model):
    trace_id: str
    run_id: str | None = None
    stages: list[StageResult] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    extracted_fields: LLMExtractedInvoice | None = None
    human_review: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ScenarioMetadata(Model):
    key: str
    name: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    expected_decision: DecisionStatus | None = None


class ProcessRequest(Model):
    file_name: str
    source_object_key: str | None = None
    scenario: ScenarioMetadata | None = None
    requested_by: str | None = None


class ReviewRequest(Model):
    action: Literal['APPROVE', 'REJECT']
    reviewer_name: str = Field(min_length=0)
    reviewer_note: str = Field(min_length=0)

    @model_validator(mode='before')
    @classmethod
    def trim_review_fields(cls, values):
        if isinstance(values, dict):
            values = dict(values)
            values['reviewer_name'] = str(values.get('reviewer_name', '')).strip()
            values['reviewer_note'] = str(values.get('reviewer_note', '')).strip()
        return values


class ProcessResponse(Model):
    run_id: str
    status: InvoiceStatus
    invoice: NormalizedInvoice | None = None
    decision: DecisionResult | None = None
    trace: AuditTrace | None = None
    extraction_metadata: ExtractionMetadata | None = None
    extracted_fields: LLMExtractedInvoice | None = None


class InvoiceRunResponse(ProcessResponse):
    id: str | None = None
    scenario_key: str | None = None
    created_at: datetime | None = None
    human_decision: str | None = None
    reviewer_name: str | None = None
    reviewer_note: str | None = None
    reviewed_at: datetime | None = None
    display_id: str | None = None
    vendor_display: str | None = None
    total_amount: Decimal | None = None


class DashboardStats(Model):
    total_runs: int = Field(default=0, ge=0)
    approved: int = Field(default=0, ge=0)
    approved_with_warning: int = Field(default=0, ge=0)
    manual_review: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    total_amount: Money = Decimal('0')
    total_by_currency: dict[str, Decimal] = Field(default_factory=dict)
    average_confidence: Confidence | None = None
    by_status: dict[str, int] = Field(default_factory=dict)
    as_of: datetime | None = None
