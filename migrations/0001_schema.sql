PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS vendors (
  id TEXT PRIMARY KEY,
  legal_name TEXT NOT NULL,
  display_name TEXT NOT NULL,
  tax_id TEXT NOT NULL UNIQUE,
  currency TEXT NOT NULL DEFAULT 'USD',
  payment_terms TEXT NOT NULL DEFAULT 'NET_30',
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'blocked')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS purchase_orders (
  id TEXT PRIMARY KEY,
  po_number TEXT NOT NULL UNIQUE,
  vendor_id TEXT NOT NULL,
  total_amount REAL NOT NULL CHECK (total_amount >= 0),
  amount_invoiced REAL NOT NULL DEFAULT 0 CHECK (amount_invoiced >= 0),
  currency TEXT NOT NULL DEFAULT 'USD',
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'cancelled')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (vendor_id) REFERENCES vendors (id)
);

CREATE TABLE IF NOT EXISTS po_line_items (
  id TEXT PRIMARY KEY,
  po_id TEXT NOT NULL,
  item_code TEXT,
  description TEXT NOT NULL,
  quantity REAL NOT NULL CHECK (quantity > 0),
  unit_price REAL NOT NULL CHECK (unit_price >= 0),
  tax_rate REAL NOT NULL DEFAULT 0 CHECK (tax_rate >= 0),
  FOREIGN KEY (po_id) REFERENCES purchase_orders (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS invoice_runs (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL UNIQUE,
  scenario_key TEXT,
  invoice_number TEXT NOT NULL,
  vendor_name TEXT NOT NULL,
  vendor_id_matched TEXT,
  po_number_matched TEXT,
  total_amount REAL NOT NULL CHECK (total_amount >= 0),
  decision TEXT NOT NULL CHECK (decision IN ('APPROVED', 'APPROVED_WITH_WARNING', 'MANUAL_REVIEW', 'REJECTED')),
  decision_reason TEXT NOT NULL,
  trace_json TEXT NOT NULL,
  source_object_key TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (vendor_id_matched) REFERENCES vendors (id)
);

CREATE INDEX IF NOT EXISTS idx_purchase_orders_vendor_status
  ON purchase_orders (vendor_id, status);

CREATE INDEX IF NOT EXISTS idx_po_line_items_po_id
  ON po_line_items (po_id);

CREATE INDEX IF NOT EXISTS idx_invoice_runs_vendor_invoice
  ON invoice_runs (vendor_id_matched, invoice_number);

CREATE INDEX IF NOT EXISTS idx_invoice_runs_created_at
  ON invoice_runs (created_at);
