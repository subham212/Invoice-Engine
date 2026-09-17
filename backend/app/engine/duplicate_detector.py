from __future__ import annotations
from decimal import Decimal
from app.models.models import DuplicateEvidence, NormalizedInvoice

def detect_duplicates(invoice: NormalizedInvoice, history: list[dict], vendor_id: str | None = None) -> list[DuplicateEvidence]:
    evidence=[]
    for row in history:
        matched=[]
        if str(row.get('invoice_number','')).casefold() == invoice.invoice_number.casefold(): matched.append('invoice_number')
        if vendor_id and row.get('vendor_id_matched') == vendor_id: matched.append('vendor_id')
        try: same_total = Decimal(str(row.get('total_amount'))) == invoice.totals.total
        except Exception: same_total = False
        if same_total: matched.append('total_amount')
        score = 100 if set(('invoice_number','vendor_id')).issubset(matched) else (90 if set(('invoice_number','total_amount')).issubset(matched) and ('vendor_id' in matched or not vendor_id) else 0)
        if score >= 70:
            evidence.append(DuplicateEvidence(invoice_run_id=str(row.get('run_id') or row.get('id','')), invoice_number=str(row.get('invoice_number','')), vendor_id=row.get('vendor_id_matched') or row.get('vendor_id'), similarity_score=Decimal(score), matched_fields=matched, explanation='Existing run shares ' + ', '.join(matched)))
    return evidence
