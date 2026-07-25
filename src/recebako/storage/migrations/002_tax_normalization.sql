PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS receipt_tax_breakdowns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    tax_rate INTEGER NOT NULL CHECK (tax_rate BETWEEN 0 AND 100),
    taxable_amount INTEGER NOT NULL,
    tax_amount INTEGER NOT NULL,
    tax_treatment TEXT NOT NULL
        CHECK (tax_treatment IN ('included', 'excluded', 'unknown'))
);

CREATE TABLE IF NOT EXISTS item_tax_details (
    item_id INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    price_raw INTEGER NOT NULL,
    tax_rate INTEGER CHECK (tax_rate BETWEEN 0 AND 100),
    tax_treatment TEXT NOT NULL
        CHECK (tax_treatment IN ('included', 'excluded', 'unknown')),
    tax_adjustment INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_receipt_tax_breakdowns_receipt_id
    ON receipt_tax_breakdowns(receipt_id);

INSERT OR IGNORE INTO schema_migrations(version)
VALUES ('002_tax_normalization');
