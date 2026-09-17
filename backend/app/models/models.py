from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


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
    tax: ExtractedField | None = None
    total: ExtractedField | None = None
    line_items: list[ExtractedField] = Field(default_factory=list)


class LineItem(Model):
    item_code: str | None = None
    description: str
    quantity: Quantity
    unit_price: Money
    tax_rate: Annotated[Decimal, Field(ge=0, le=100, max_digits=7, decimal_places=4)] = Decimal('0')
    amount: Money | None = None


class InvoiceTotals(Model):
    subtotal: Money
    tax: Money = Decimal('0')
    total: Money
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


class AuditTrace(Model):
    trace_id: str
    run_id: str | None = None
    stages: list[StageResult] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
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


class ProcessResponse(Model):
    run_id: str
    status: InvoiceStatus
    invoice: NormalizedInvoice | None = None
    decision: DecisionResult | None = None
    trace: AuditTrace | None = None


class InvoiceRunResponse(ProcessResponse):
    id: str | None = None
    scenario_key: str | None = None
    created_at: datetime | None = None


class DashboardStats(Model):
    total_runs: int = Field(default=0, ge=0)
    approved: int = Field(default=0, ge=0)
    approved_with_warning: int = Field(default=0, ge=0)
    manual_review: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    total_amount: Money = Decimal('0')
    average_confidence: Confidence | None = None
    by_status: dict[str, int] = Field(default_factory=dict)
    as_of: datetime | None = None
