from __future__ import annotations

from decimal import Decimal

from app.models.models import DecisionResult, DecisionStatus, ValidationCheck, ValidationStatus, VendorStatus

NON_PO_AUTO_APPROVE_LIMIT = Decimal('500.00')


class DecisionArbiter:
    def decide(self, checks: list[ValidationCheck], duplicate_evidence=None, vendor_confidence=100,
               po_message='', invoice_total=Decimal('0'), vendor_status=None, missing_fields=None) -> DecisionResult:
        duplicate_evidence = duplicate_evidence or []
        missing_fields = missing_fields or []
        failures = [check for check in checks if check.status == ValidationStatus.FAIL]
        warnings = [check for check in checks if check.status == ValidationStatus.WARN]
        blocked = vendor_status == VendorStatus.BLOCKED or vendor_status == VendorStatus.BLOCKED.value
        po_hard_cap = any(check.name == 'po_hard_cap' for check in failures)
        missing_po = po_message == 'Purchase order is missing'

        if duplicate_evidence:
            decision, reason = DecisionStatus.REJECTED, f'Duplicate invoice detected: {duplicate_evidence[0].explanation}'
        elif blocked:
            decision, reason = DecisionStatus.REJECTED, 'Vendor is blocked; do not pay this invoice.'
        elif po_hard_cap:
            decision, reason = DecisionStatus.REJECTED, next(check.message for check in failures if check.name == 'po_hard_cap')
        elif missing_po and invoice_total > NON_PO_AUTO_APPROVE_LIMIT:
            decision, reason = DecisionStatus.MANUAL_REVIEW, 'Missing Purchase Order — invoice exceeds $500 non-PO auto-approval threshold; routed for AP manager approval.'
        elif missing_po and vendor_status not in (VendorStatus.ACTIVE, VendorStatus.ACTIVE.value):
            decision, reason = DecisionStatus.MANUAL_REVIEW, 'Missing Purchase Order — vendor is not approved; route for AP manager approval.'
        elif vendor_confidence < 70:
            decision, reason = DecisionStatus.MANUAL_REVIEW, 'Vendor identity could not be established confidently; verify the supplier before payment.'
        elif missing_fields:
            decision, reason = DecisionStatus.MANUAL_REVIEW, 'Required invoice fields are missing; review the document and complete the record.'
        elif failures:
            line_failure = next((check for check in failures if check.name.startswith('po_line_') or check.name == 'line_reconciliation'), None)
            reason = (f'Purchase order line reconciliation failed: {line_failure.message} Review the variance before payment.'
                      if line_failure else 'A financial or purchase order check failed; review the listed variance before payment.')
            decision = DecisionStatus.MANUAL_REVIEW
        elif missing_po:
            decision, reason = DecisionStatus.APPROVED_WITH_WARNING, 'Approved under $500 non-PO micro-spend policy; record the business justification.'
        elif warnings:
            partial = next((check for check in warnings if check.name == 'partial_billing'), None)
            reason = (f'{partial.message} The invoice is approved with a warning; the remaining PO balance stays available for later invoices.'
                      if partial else 'Invoice passed the required checks with warnings; review the audit details.')
            decision = DecisionStatus.APPROVED_WITH_WARNING
        else:
            decision, reason = DecisionStatus.APPROVED, 'All vendor, purchase order, duplicate, and arithmetic checks passed.'

        next_action = {
            DecisionStatus.APPROVED: 'No further action is required.',
            DecisionStatus.APPROVED_WITH_WARNING: 'Review the warning and record any required justification.',
            DecisionStatus.MANUAL_REVIEW: 'An AP manager must review the invoice before payment.',
            DecisionStatus.REJECTED: 'Do not pay this invoice; resolve the rejection reason first.',
        }[decision]
        summary = f'{reason} {next_action}'
        return DecisionResult(decision=decision, reason=reason, executive_summary=summary,
                              confidence=min(100, vendor_confidence), validation_checks=checks,
                              duplicate_evidence=duplicate_evidence)
