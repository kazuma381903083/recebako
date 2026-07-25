PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store TEXT NOT NULL,
    date_raw TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    total INTEGER NOT NULL,
    subtotal INTEGER NOT NULL,
    tax INTEGER NOT NULL,
    payment TEXT NOT NULL,
    category TEXT,
    status TEXT NOT NULL
        CHECK (status IN ('confirmed', 'review', 'failed')),
    confidence REAL NOT NULL,
    phash TEXT NOT NULL,
    image_path TEXT NOT NULL,
    ingest_mode TEXT NOT NULL
        CHECK (ingest_mode IN ('regular', 'historical')),
    validation_issues_json TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL,
    duplicate_of_id INTEGER REFERENCES receipts(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    name_norm TEXT,
    qty INTEGER NOT NULL,
    price INTEGER NOT NULL,
    category TEXT
);

CREATE TABLE IF NOT EXISTS store_master (
    store_pattern TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_receipts_identity
    ON receipts(store, date, total);
CREATE INDEX IF NOT EXISTS idx_receipts_phash
    ON receipts(phash);
CREATE INDEX IF NOT EXISTS idx_receipts_duplicate_of_id
    ON receipts(duplicate_of_id);
CREATE INDEX IF NOT EXISTS idx_items_receipt_id
    ON items(receipt_id);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('001_initial');
