-- Existing databases are upgraded idempotently by D1Client.ensure_schema at API startup.
-- This SQL documents the resulting schema for fresh or manually managed D1 databases.
ALTER TABLE invoice_runs ADD COLUMN currency TEXT NOT NULL DEFAULT 'USD';
ALTER TABLE invoice_runs ADD COLUMN extraction_method TEXT NOT NULL DEFAULT 'pdfplumber_fallback';
ALTER TABLE invoice_runs ADD COLUMN model_used TEXT;
ALTER TABLE invoice_runs ADD COLUMN extraction_latency_ms INTEGER NOT NULL DEFAULT 0;
ALTER TABLE invoice_runs ADD COLUMN is_scanned INTEGER NOT NULL DEFAULT 0;
ALTER TABLE invoice_runs ADD COLUMN executive_summary TEXT NOT NULL DEFAULT '';
ALTER TABLE invoice_runs ADD COLUMN human_decision TEXT;
ALTER TABLE invoice_runs ADD COLUMN reviewer_name TEXT;
ALTER TABLE invoice_runs ADD COLUMN reviewer_note TEXT;
ALTER TABLE invoice_runs ADD COLUMN reviewed_at TEXT;
ALTER TABLE invoice_runs ADD COLUMN invoice_json TEXT;
ALTER TABLE invoice_runs ADD COLUMN po_credited INTEGER NOT NULL DEFAULT 0;
ALTER TABLE invoice_runs ADD COLUMN credit_amount REAL NOT NULL DEFAULT 0;
ALTER TABLE purchase_orders ADD COLUMN base_amount_invoiced REAL NOT NULL DEFAULT -1;
UPDATE purchase_orders SET base_amount_invoiced = amount_invoiced WHERE base_amount_invoiced < 0;

CREATE TABLE purchase_orders_new (
  id TEXT PRIMARY KEY,
  po_number TEXT NOT NULL UNIQUE,
  vendor_id TEXT NOT NULL,
  total_amount REAL NOT NULL CHECK (total_amount >= 0),
  amount_invoiced REAL NOT NULL DEFAULT 0 CHECK (amount_invoiced >= 0),
  base_amount_invoiced REAL NOT NULL DEFAULT 0,
  currency TEXT NOT NULL DEFAULT 'USD',
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','partially_utilized','closed','cancelled')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (vendor_id) REFERENCES vendors (id)
);
INSERT INTO purchase_orders_new SELECT id, po_number, vendor_id, total_amount, amount_invoiced, base_amount_invoiced, currency, status, created_at FROM purchase_orders;
CREATE TABLE po_line_items_new (
  id TEXT PRIMARY KEY,
  po_id TEXT NOT NULL,
  item_code TEXT,
  description TEXT NOT NULL,
  quantity REAL NOT NULL CHECK (quantity > 0),
  unit_price REAL NOT NULL CHECK (unit_price >= 0),
  tax_rate REAL NOT NULL DEFAULT 0 CHECK (tax_rate >= 0),
  FOREIGN KEY (po_id) REFERENCES purchase_orders_new (id) ON DELETE CASCADE
);
INSERT INTO po_line_items_new SELECT id, po_id, item_code, description, quantity, unit_price, tax_rate FROM po_line_items;
DROP TABLE po_line_items;
DROP TABLE purchase_orders;
ALTER TABLE purchase_orders_new RENAME TO purchase_orders;
ALTER TABLE po_line_items_new RENAME TO po_line_items;
CREATE INDEX IF NOT EXISTS idx_purchase_orders_vendor_status ON purchase_orders (vendor_id, status);
CREATE INDEX IF NOT EXISTS idx_po_line_items_po_id ON po_line_items (po_id);
UPDATE purchase_orders
SET status = CASE WHEN amount_invoiced >= total_amount THEN 'closed'
                  WHEN amount_invoiced > 0 THEN 'partially_utilized' ELSE 'open' END;
