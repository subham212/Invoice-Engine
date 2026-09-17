INSERT OR IGNORE INTO vendors (id, legal_name, display_name, tax_id, currency, payment_terms, status) VALUES
  ('vendor-acme', 'Acme Corporation Inc.', 'Acme Corp', 'US-EIN-821928371', 'USD', 'NET_30', 'active'),
  ('vendor-apex', 'Apex Engineering LLC', 'Apex Engineering', 'US-EIN-441025610', 'USD', 'NET_45', 'active'),
  ('vendor-global', 'Global Supplies LLC', 'Global Supplies', 'US-EIN-991200114', 'USD', 'NET_30', 'active'),
  ('vendor-novas', 'Novas Industrial Inc.', 'Novas Industrial', 'US-EIN-772900882', 'USD', 'NET_30', 'active'),
  ('vendor-stealth', 'Stealth Tech Labs Inc.', 'Stealth Tech Labs', 'US-EIN-009100723', 'USD', 'NET_60', 'active');

INSERT OR IGNORE INTO purchase_orders (id, po_number, vendor_id, total_amount, amount_invoiced, currency, status) VALUES
  ('po-acme', 'PO-8820', 'vendor-acme', 10000.00, 0.00, 'USD', 'open'),
  ('po-apex', 'PO-4410', 'vendor-apex', 50000.00, 0.00, 'USD', 'open'),
  ('po-global', 'PO-9912', 'vendor-global', 12000.00, 0.00, 'USD', 'open'),
  ('po-novas', 'PO-7729', 'vendor-novas', 4500.00, 4500.00, 'USD', 'closed'),
  ('po-stealth', 'PO-1204', 'vendor-stealth', 8400.00, 0.00, 'USD', 'open');

INSERT OR IGNORE INTO po_line_items (id, po_id, item_code, description, quantity, unit_price, tax_rate) VALUES
  ('line-acme-1', 'po-acme', 'LIC-ENT', 'Enterprise cloud licenses', 5, 2000.00, 0),
  ('line-apex-1', 'po-apex', 'SRV-RACK', 'Server racks', 10, 5000.00, 0),
  ('line-global-1', 'po-global', 'OFF-PAPER', 'Office paper boxes', 1000, 12.00, 0),
  ('line-novas-1', 'po-novas', 'MFG-PART', 'Industrial replacement parts', 15, 300.00, 0),
  ('line-stealth-1', 'po-stealth', 'SEC-AUDIT', 'Security audit package', 1, 8400.00, 0);

INSERT OR IGNORE INTO invoice_runs (
  id, run_id, scenario_key, invoice_number, vendor_name, vendor_id_matched,
  po_number_matched, total_amount, decision, decision_reason, trace_json, created_at
) VALUES (
  'run-novas-historical', 'RUN-HIST-7729', 'duplicate-invoice', 'INV-7729',
  'Novas Industrial Inc.', 'vendor-novas', 'PO-7729', 4500.00, 'APPROVED',
  'Historical payment retained for duplicate detection.', '{"source":"seed","paid":true}',
  '2026-09-02 10:00:00'
);
