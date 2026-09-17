from __future__ import annotations
import re
from difflib import SequenceMatcher
from app.models.models import Vendor, VendorStatus

def _norm(value: str) -> str:
    return re.sub(r'[^a-z0-9]', '', value.lower())

def match_vendor(name: str, vendors: list[Vendor], tax_id: str | None = None) -> tuple[Vendor | None, int, str]:
    if tax_id:
        exact = [v for v in vendors if v.tax_id and _norm(v.tax_id) == _norm(tax_id)]
        if len(exact) == 1: return exact[0], 100, 'Vendor tax ID exactly matched'
    scored = [(max(SequenceMatcher(None, _norm(name), _norm(v.legal_name)).ratio(), SequenceMatcher(None, _norm(name), _norm(v.display_name)).ratio()), v) for v in vendors]
    if not scored: return None, 0, 'No vendors available for matching'
    score, vendor = max(scored, key=lambda x: x[0]); points = round(score * 100)
    if points < 70: return None, points, f'No sufficiently confident vendor match ({points}%)'
    if vendor.status != VendorStatus.ACTIVE: return vendor, points, f'Vendor is {vendor.status}'
    return vendor, points, f'Matched vendor {vendor.display_name} ({points}%)'
