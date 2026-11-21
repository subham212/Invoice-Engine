from __future__ import annotations

from app.models.models import LineReconciliation, NormalizedInvoice, PurchaseOrder, ValidationStatus
from .tolerance_rules import ToleranceRules


class PurchaseOrderMatcher:
    def __init__(self, rules: ToleranceRules | None = None):
        self.rules = rules or ToleranceRules()

    def resolve(self, invoice: NormalizedInvoice, purchase_orders: list[PurchaseOrder], vendor_id: str | None) -> tuple[PurchaseOrder | None, list[LineReconciliation], str]:
        if not invoice.purchase_order_number:
            return None, [], 'Purchase order is missing'
        candidates = [po for po in purchase_orders if po.po_number.casefold() == invoice.purchase_order_number.casefold()]
        if not candidates:
            return None, [], 'Purchase order number was provided but not found'
        po = candidates[0]
        if vendor_id and po.vendor_id != vendor_id:
            return po, [], 'Purchase order belongs to a different vendor'
        if po.status in {'closed', 'cancelled'} or po.amount_invoiced >= po.total_amount:
            return po, [], 'Purchase order is exhausted or closed'
        reconciliations = []
        for line in invoice.line_items:
            match = next((item for item in po.line_items if (line.item_code and item.item_code == line.item_code)
                          or item.description.casefold() == line.description.casefold()), None)
            if not match:
                reconciliations.append(LineReconciliation(
                    quantity_invoiced=line.quantity, quantity_ordered=0, unit_price_invoiced=line.unit_price,
                    unit_price_ordered=0, quantity_variance=line.quantity, price_variance=100,
                    status=ValidationStatus.FAIL, explanation='Invoice line is absent from the purchase order',
                ))
                continue
            qv, pv, status, explanation = self.rules.compare_line(line.quantity, line.unit_price, match.quantity, match.unit_price, invoice.totals.currency)
            reconciliations.append(LineReconciliation(
                po_line_id=match.id, quantity_invoiced=line.quantity, quantity_ordered=match.quantity,
                unit_price_invoiced=line.unit_price, unit_price_ordered=match.unit_price,
                quantity_variance=qv, price_variance=pv, status=status, explanation=explanation,
            ))
        remaining = max(0, po.total_amount - po.amount_invoiced)
        remaining_text = f'${remaining:,.2f}' if invoice.totals.currency == 'USD' else f'{invoice.totals.currency} {remaining:,.2f}'
        return po, reconciliations, f'Purchase order matched; {remaining_text} remains before this invoice'
