from typing import Any

SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        'key': 'happy_path',
        'name': 'Happy path',
        'description': 'Invoice with a valid vendor and purchase order.',
        'tags': ['approved', 'three-way match'],
        'expected_decision': 'APPROVED',
        'sample_file': 'happy_path.pdf',
    },
    {
        'key': 'partial_billing',
        'name': 'Partial billing',
        'description': 'Invoice that bills only part of the available purchase order.',
        'tags': ['partial', 'purchase order'],
        'expected_decision': 'APPROVED_WITH_WARNING',
        'sample_file': 'partial_billing.pdf',
    },
    {
        'key': 'price_creep',
        'name': 'Price creep',
        'description': 'Invoice with a unit price variance against the purchase order.',
        'tags': ['variance', 'reconciliation'],
        'expected_decision': 'MANUAL_REVIEW',
        'sample_file': 'price_creep.pdf',
    },
    {
        'key': 'duplicate',
        'name': 'Duplicate invoice',
        'description': 'Invoice matching a prior invoice already in the ledger.',
        'tags': ['duplicate', 'risk'],
        'expected_decision': 'REJECTED',
        'sample_file': 'duplicate.pdf',
    },
    {
        'key': 'missing_po',
        'name': 'Missing purchase order',
        'description': 'Invoice without a resolvable purchase order.',
        'tags': ['exception', 'manual review'],
        'expected_decision': 'APPROVED_WITH_WARNING',
        'sample_file': 'missing_po.pdf',
    },
)


def scenario_metadata() -> list[dict[str, Any]]:
    return [dict(scenario) for scenario in SCENARIOS]
