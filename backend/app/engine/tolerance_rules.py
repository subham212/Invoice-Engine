from __future__ import annotations

from decimal import Decimal

from app.models.models import NormalizedInvoice, ValidationCheck, ValidationStatus


class ToleranceRules:
    def __init__(self, amount_tolerance: Decimal = Decimal('0.02'), quantity_tolerance: Decimal = Decimal('0'), price_percent: Decimal = Decimal('2')):
        self.amount_tolerance = amount_tolerance
        self.quantity_tolerance = quantity_tolerance
        self.price_percent = price_percent

    def check(self, invoice: NormalizedInvoice) -> list[ValidationCheck]:
        checks = []
        for index, item in enumerate(invoice.line_items, 1):
            expected = item.quantity * item.unit_price
            actual = item.amount if item.amount is not None else expected
            delta = abs(expected - actual)
            checks.append(ValidationCheck(
                name=f'line_amount_math_{index}',
                status=ValidationStatus.PASS if delta <= self.amount_tolerance else ValidationStatus.FAIL,
                message=f'Line {index}: {item.quantity} × {item.unit_price} = {expected}; printed amount {actual} (variance {delta})',
                expected=expected, actual=actual,
            ))
        computed = sum((item.amount if item.amount is not None else item.quantity * item.unit_price for item in invoice.line_items), Decimal('0'))
        checks.append(ValidationCheck(
            name='subtotal_math', status=ValidationStatus.PASS if abs(computed - invoice.totals.subtotal) <= self.amount_tolerance else ValidationStatus.FAIL,
            message=f'Line amounts total {computed}; printed subtotal {invoice.totals.subtotal} (variance {abs(computed - invoice.totals.subtotal)})',
            expected=invoice.totals.subtotal, actual=computed,
        ))
        expected_total = invoice.totals.subtotal + invoice.totals.tax
        checks.append(ValidationCheck(
            name='total_math', status=ValidationStatus.PASS if abs(expected_total - invoice.totals.total) <= self.amount_tolerance else ValidationStatus.FAIL,
            message=f'Subtotal {invoice.totals.subtotal} + tax {invoice.totals.tax} = {expected_total}; printed total {invoice.totals.total} (variance {abs(expected_total - invoice.totals.total)})',
            expected=invoice.totals.total, actual=expected_total,
        ))
        return checks

    def compare_line(self, quantity, price, po_quantity, po_price):
        quantity_variance = quantity - po_quantity
        price_variance = (price - po_price) / po_price * 100 if po_price else (Decimal('0') if price == 0 else Decimal('100'))
        status = ValidationStatus.PASS if quantity_variance <= self.quantity_tolerance and abs(price_variance) <= self.price_percent else ValidationStatus.FAIL
        message = (f'Unit price ${price:.2f} vs PO ${po_price:.2f}: {price_variance:+.2f}% '
                   f'(${price - po_price:+.2f} per unit); allowed ±{self.price_percent:.2f}%')
        return quantity_variance, price_variance, status, message
