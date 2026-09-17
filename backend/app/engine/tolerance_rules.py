from __future__ import annotations
from decimal import Decimal
from app.models.models import ValidationCheck, ValidationStatus, NormalizedInvoice

class ToleranceRules:
    def __init__(self, amount_tolerance: Decimal = Decimal('0.02'), quantity_tolerance: Decimal = Decimal('0'), price_percent: Decimal = Decimal('5')):
        self.amount_tolerance, self.quantity_tolerance, self.price_percent = amount_tolerance, quantity_tolerance, price_percent
    def check(self, invoice: NormalizedInvoice) -> list[ValidationCheck]:
        computed = sum((i.amount if i.amount is not None else i.quantity*i.unit_price for i in invoice.line_items), Decimal('0'))
        checks=[ValidationCheck(name='subtotal_math', status=ValidationStatus.PASS if abs(computed-invoice.totals.subtotal)<=self.amount_tolerance else ValidationStatus.FAIL, message=f'Line subtotal {computed} vs stated {invoice.totals.subtotal}', expected=invoice.totals.subtotal, actual=computed)]
        total_expected=invoice.totals.subtotal+invoice.totals.tax
        checks.append(ValidationCheck(name='total_math', status=ValidationStatus.PASS if abs(total_expected-invoice.totals.total)<=self.amount_tolerance else ValidationStatus.FAIL, message=f'Subtotal plus tax {total_expected} vs total {invoice.totals.total}', expected=invoice.totals.total, actual=total_expected))
        return checks
    def compare_line(self, quantity, price, po_quantity, po_price):
        qv=quantity-po_quantity; pv=(price-po_price)/po_price*100 if po_price else (Decimal('0') if price==0 else Decimal('100'))
        status=ValidationStatus.PASS if qv <= self.quantity_tolerance and abs(pv) <= self.price_percent else ValidationStatus.FAIL
        return qv, pv, status
