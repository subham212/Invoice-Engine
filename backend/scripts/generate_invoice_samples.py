from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUTPUT_DIR = Path(__file__).resolve().parents[1] / 'generated_samples'

SCENARIOS = [
    ('happy_path', 'INV-2026-1001', 'Acme Corp', 'PO-8820', [('Enterprise cloud licenses', 5, 2000.00)]),
    ('partial_billing', 'INV-2026-1002', 'Apex Engineering', 'PO-4410', [('Server racks', 2, 5000.00)]),
    ('price_creep', 'INV-2026-1003', 'Global Supplies', 'PO-9912', [('Office paper boxes', 10, 13.00)]),
    ('duplicate', 'INV-7729', 'Novas Industrial Inc.', 'PO-7729', [('Industrial replacement parts', 15, 300.00)]),
    ('missing_po', 'INV-2026-1005', 'Stealth Tech Labs', '', [('Security audit package', 1, 8400.00)]),
]


def create_pdf(scenario: str, invoice_number: str, vendor: str, po_number: str, items: list[tuple[str, int, float]]) -> Path:
    path = OUTPUT_DIR / f'{scenario}.pdf'
    doc = SimpleDocTemplate(str(path), pagesize=letter, rightMargin=.65 * inch, leftMargin=.65 * inch, topMargin=.6 * inch, bottomMargin=.6 * inch)
    styles = getSampleStyleSheet()
    story = [Paragraph('ZAMP SAMPLE INVOICE', styles['Title']), Spacer(1, 12)]
    story.append(Paragraph(f'<b>Vendor:</b> {vendor}<br/><b>Invoice:</b> {invoice_number}<br/><b>Invoice date:</b> 2026-09-15<br/><b>Purchase order:</b> {po_number or "Not provided"}', styles['BodyText']))
    story.append(Spacer(1, 18))
    rows = [['Description', 'Quantity', 'Unit price', 'Amount']]
    subtotal = 0.0
    for description, quantity, price in items:
        amount = quantity * price
        subtotal += amount
        rows.append([description, str(quantity), f'${price:,.2f}', f'${amount:,.2f}'])
    rows.extend([['', '', 'Subtotal', f'${subtotal:,.2f}'], ['', '', 'Tax (8.25%)', f'${subtotal * .0825:,.2f}'], ['', '', 'Total', f'${subtotal * 1.0825:,.2f}']])
    table = Table(rows, colWidths=[3.45 * inch, .8 * inch, 1.1 * inch, 1.15 * inch])
    table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#19324d')), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white), ('GRID', (0, 0), (-1, -4), .5, colors.grey), ('ALIGN', (1, 1), (-1, -1), 'RIGHT'), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTNAME', (-2, -3), (-1, -1), 'Helvetica-Bold'), ('LINEABOVE', (-2, -1), (-1, -1), 1, colors.black), ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7)]))
    story.extend([table, Spacer(1, 18), Paragraph('Payment terms: Net 30. This document is synthetic and intended only for invoice-processing demonstrations.', styles['Italic'])])
    doc.build(story)
    return path


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for scenario, invoice, vendor, po, items in SCENARIOS:
        create_pdf(scenario, invoice, vendor, po, items)
    print(f'Generated {len(SCENARIOS)} synthetic invoice PDFs in {OUTPUT_DIR}')


if __name__ == '__main__':
    main()
