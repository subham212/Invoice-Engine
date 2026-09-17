from __future__ import annotations
from app.models.models import DecisionResult, DecisionStatus, ValidationCheck, ValidationStatus

class DecisionArbiter:
    def decide(self, checks: list[ValidationCheck], duplicate_evidence=None, vendor_confidence=100, po_message='') -> DecisionResult:
        duplicate_evidence=duplicate_evidence or []
        failures=[c for c in checks if c.status==ValidationStatus.FAIL]; warnings=[c for c in checks if c.status==ValidationStatus.WARN]
        if duplicate_evidence: decision=DecisionStatus.REJECTED; reason='Hard duplicate detected: ' + duplicate_evidence[0].explanation
        elif vendor_confidence < 70: decision=DecisionStatus.MANUAL_REVIEW; reason='Vendor identity could not be established confidently'
        elif failures: decision=DecisionStatus.MANUAL_REVIEW; reason='Validation failed: ' + failures[0].message
        elif warnings or (po_message and 'missing' in po_message.lower()): decision=DecisionStatus.APPROVED_WITH_WARNING; reason=po_message or 'Approved with validation warnings'
        else: decision=DecisionStatus.APPROVED; reason='All invoice, vendor, PO, and arithmetic checks passed'
        return DecisionResult(decision=decision, reason=reason, confidence=min(100, vendor_confidence), validation_checks=checks, duplicate_evidence=duplicate_evidence)
