PRAGMA foreign_keys = ON;

ALTER TABLE receipts
ADD COLUMN file_state TEXT NOT NULL DEFAULT 'finalized'
    CHECK (file_state IN ('pending', 'finalized'));

UPDATE receipts
SET file_state = 'pending'
WHERE image_path LIKE 'processing/%';

INSERT INTO schema_migrations(version)
VALUES ('003_receipt_file_state');
