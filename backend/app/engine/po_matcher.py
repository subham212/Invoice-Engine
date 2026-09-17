from __future__ import annotations
from app.models.models import PurchaseOrder, NormalizedInvoice, LineReconciliation, ValidationStatus
from .tolerance_rules import ToleranceRules

class PurchaseOrderMatcher:
    def __init__(self, rules: ToleranceRules | None = None): self.rules = rules or ToleranceRules()
    def resolve(self, invoice: NormalizedInvoice, purchase_orders: list[PurchaseOrder], vendor_id: str | None) -> tuple[PurchaseOrder | None, list[LineReconciliation], str]:
        if invoice.purchase_order_number:
            candidates=[p for p in purchase_orders if p.po_number.casefold()==invoice.purchase_order_number.casefold()]
            if not candidates: return None, [], 'Purchase order number was provided but not found'
        else:
            return None, [], 'Purchase order is missing or cannot be resolved unambiguously'
        po=candidates[0]
        if vendor_id and po.vendor_id != vendor_id: return po, [], 'Purchase order belongs to a different vendor'
        rec=[]
        for i, line in enumerate(invoice.line_items):
            match=next((x for x in po.line_items if (line.item_code and x.item_code==line.item_code) or x.description.casefold()==line.description.casefold()), None)
            if not match: rec.append(LineReconciliation(quantity_invoiced=line.quantity, quantity_ordered=0, unit_price_invoiced=line.unit_price, unit_price_ordered=0, quantity_variance=line.quantity, price_variance=100, status=ValidationStatus.FAIL, explanation='Invoice line is absent from PO')); continue
            qv,pv,status=self.rules.compare_line(line.quantity,line.unit_price,match.quantity,match.unit_price)
            rec.append(LineReconciliation(invoice_line_id=None, po_line_id=match.id, quantity_invoiced=line.quantity, quantity_ordered=match.quantity, unit_price_invoiced=line.unit_price, unit_price_ordered=match.unit_price, quantity_variance=qv, price_variance=pv, status=status, explanation='Line matched by item code or description'))
        return po, rec, 'Purchase order matched'
