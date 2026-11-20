from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.models.models import Vendor, VendorStatus

_SUFFIXES = re.compile(r'\b(?:private\s+limited|pvt\.?\s*ltd\.?|incorporated|corporation|corp\.?|limited|ltd\.?|llc|inc\.?)\b', re.I)


def _norm(value: str) -> str:
    return re.sub(r'[^a-z0-9]', '', _SUFFIXES.sub('', value.lower()))


def match_vendor(name: str, vendors: list[Vendor], tax_id: str | None = None) -> tuple[Vendor | None, int, str]:
    if tax_id:
        exact = [vendor for vendor in vendors if vendor.tax_id and _norm(vendor.tax_id) == _norm(tax_id)]
        if len(exact) == 1:
            vendor = exact[0]
            return vendor, 100, f'Exact tax ID match: {vendor.display_name} ({vendor.status})'
    scored = [(max(SequenceMatcher(None, _norm(name), _norm(vendor.legal_name)).ratio(),
                   SequenceMatcher(None, _norm(name), _norm(vendor.display_name)).ratio()), vendor) for vendor in vendors]
    if not scored:
        return None, 0, 'No registered vendors are available'
    score, vendor = max(scored, key=lambda item: item[0])
    points = round(score * 100)
    if points < 70:
        return None, points, f'No sufficiently confident vendor match ({points}%)'
    return vendor, points, f'Matched {vendor.display_name} by name ({points}% confidence, {vendor.status})'
