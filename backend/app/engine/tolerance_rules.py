from __future__ import annotations

from decimal import Decimal

from app.models.models import NormalizedInvoice, ValidationCheck, ValidationStatus


def _money(value: Decimal, currency: str) -> str:
    return f'${value:,.2f}' if currency == 'USD' else f'{currency} {value:,.2f}'


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
                message=f'Line {index}: {item.quantity} × {_money(item.unit_price, invoice.totals.currency)} = {_money(expected, invoice.totals.currency)}; printed amount {_money(actual, invoice.totals.currency)} (variance {_money(delta, invoice.totals.currency)})',
                expected=expected, actual=actual,
            ))
        computed = sum((item.amount if item.amount is not None else item.quantity * item.unit_price for item in invoice.line_items), Decimal('0'))
        checks.append(ValidationCheck(
            name='subtotal_math', status=ValidationStatus.PASS if abs(computed - invoice.totals.subtotal) <= self.amount_tolerance else ValidationStatus.FAIL,
            message=f'Line amounts total {_money(computed, invoice.totals.currency)}; printed subtotal {_money(invoice.totals.subtotal, invoice.totals.currency)} (variance {_money(abs(computed - invoice.totals.subtotal), invoice.totals.currency)})',
            expected=invoice.totals.subtotal, actual=computed,
        ))
        expected_total = invoice.totals.subtotal + invoice.totals.tax
        checks.append(ValidationCheck(
            name='total_math', status=ValidationStatus.PASS if abs(expected_total - invoice.totals.total) <= self.amount_tolerance else ValidationStatus.FAIL,
            message=f'Subtotal {_money(invoice.totals.subtotal, invoice.totals.currency)} + tax {_money(invoice.totals.tax, invoice.totals.currency)} = {_money(expected_total, invoice.totals.currency)}; printed total {_money(invoice.totals.total, invoice.totals.currency)} (variance {_money(abs(expected_total - invoice.totals.total), invoice.totals.currency)})',
            expected=invoice.totals.total, actual=expected_total,
        ))
        if invoice.totals.total <= 0 or any(item.amount is not None and item.amount < 0 for item in invoice.line_items):
            checks.append(ValidationCheck(name='non_positive_total', status=ValidationStatus.FAIL,
                message='Zero or credit-memo totals and negative line amounts require AP verification before payment.',
                expected='positive invoice total', actual=invoice.totals.total))
        return checks

    def compare_line(self, quantity, price, po_quantity, po_price, currency='USD'):
        quantity_variance = quantity - po_quantity
        price_variance = (price - po_price) / po_price * 100 if po_price else (Decimal('0') if price == 0 else Decimal('100'))
        status = ValidationStatus.PASS if quantity_variance <= self.quantity_tolerance and abs(price_variance) <= self.price_percent else ValidationStatus.FAIL
        marker = '$' if currency == 'USD' else f'{currency} '
        message = (f'Unit price {marker}{price:,.2f} vs PO {marker}{po_price:,.2f}: {price_variance:+.2f}% '
                   f'({marker}{price - po_price:+,.2f} per unit); allowed ±{self.price_percent:.2f}%')
        return quantity_variance, price_variance, status, message
