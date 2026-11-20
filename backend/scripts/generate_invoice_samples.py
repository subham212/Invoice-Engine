from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.pdfgen import canvas

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


def create_scanned_pdf() -> Path:
    width, height = 1275, 1650
    image = Image.new('RGB', (width, height), '#f8f6ef')
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=30)
    small = ImageFont.load_default(size=25)
    draw.text((110, 110), 'INVOICE', fill='#17283b', font=ImageFont.load_default(size=64))
    draw.text((110, 250), 'Vendor: Acme Corp', fill='#17283b', font=font)
    draw.text((110, 310), 'Invoice: INV-2026-1006', fill='#17283b', font=font)
    draw.text((110, 370), 'Invoice date: 2026-09-15', fill='#17283b', font=font)
    draw.text((110, 430), 'Purchase order: PO-8820', fill='#17283b', font=font)
    draw.line((100, 520, 1170, 520), fill='#536273', width=3)
    draw.text((110, 565), 'Description             Quantity       Unit price         Amount', fill='#17283b', font=small)
    draw.line((100, 620, 1170, 620), fill='#536273', width=2)
    draw.text((110, 680), 'Enterprise cloud licenses     5       $2,000.00      $10,000.00', fill='#17283b', font=small)
    draw.text((720, 950), 'Subtotal: $10,000.00', fill='#17283b', font=font)
    draw.text((720, 1020), 'Tax (8.25%): $825.00', fill='#17283b', font=font)
    draw.text((720, 1090), 'Total: $10,825.00', fill='#17283b', font=font)
    draw.text((110, 1450), 'Synthetic scanned invoice for demonstration.', fill='#596575', font=small)
    image = image.rotate(1.5, resample=Image.Resampling.BICUBIC, expand=False, fillcolor='#e8e4db')
    path = OUTPUT_DIR / 'scanned_invoice.pdf'
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.drawImage(ImageReader(image), 0, 0, width=letter[0], height=letter[1])
    pdf.showPage()
    pdf.save()
    return path


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for scenario, invoice, vendor, po, items in SCENARIOS:
        create_pdf(scenario, invoice, vendor, po, items)
    create_scanned_pdf()
    print(f'Generated {len(SCENARIOS) + 1} synthetic invoice PDFs in {OUTPUT_DIR}')


if __name__ == '__main__':
    main()
