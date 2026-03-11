PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    part_number TEXT NOT NULL,
    description TEXT,
    manufacturer TEXT,
    category TEXT,
    uom TEXT,
    normalized_part_number TEXT NOT NULL,
    normalized_description TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(normalized_part_number)
);

CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    warehouse_code TEXT,
    location_code TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(warehouse_code, location_code)
);

CREATE TABLE IF NOT EXISTS inventory_balances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    part_id INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    qty_on_hand REAL NOT NULL DEFAULT 0,
    row_version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(part_id, location_id),
    FOREIGN KEY(part_id) REFERENCES parts(id),
    FOREIGN KEY(location_id) REFERENCES locations(id)
);

CREATE TABLE IF NOT EXISTS inventory_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    part_id INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    transaction_type TEXT NOT NULL,
    qty_change REAL NOT NULL,
    reference TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    FOREIGN KEY(part_id) REFERENCES parts(id),
    FOREIGN KEY(location_id) REFERENCES locations(id)
);

CREATE TABLE IF NOT EXISTS import_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL,
    created_by TEXT,
    source_filename TEXT,
    rows_read INTEGER NOT NULL DEFAULT 0,
    rows_imported INTEGER NOT NULL DEFAULT 0,
    balances_zeroed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE IF NOT EXISTS parts_fts USING fts5(
    part_number,
    description,
    content='parts',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS parts_ai AFTER INSERT ON parts BEGIN
    INSERT INTO parts_fts(rowid, part_number, description)
    VALUES (new.id, new.part_number, COALESCE(new.description, ''));
END;

CREATE TRIGGER IF NOT EXISTS parts_ad AFTER DELETE ON parts BEGIN
    INSERT INTO parts_fts(parts_fts, rowid, part_number, description)
    VALUES ('delete', old.id, old.part_number, COALESCE(old.description, ''));
END;

CREATE TRIGGER IF NOT EXISTS parts_au AFTER UPDATE ON parts BEGIN
    INSERT INTO parts_fts(parts_fts, rowid, part_number, description)
    VALUES ('delete', old.id, old.part_number, COALESCE(old.description, ''));
    INSERT INTO parts_fts(rowid, part_number, description)
    VALUES (new.id, new.part_number, COALESCE(new.description, ''));
END;

CREATE INDEX IF NOT EXISTS idx_parts_normalized_part_number ON parts(normalized_part_number);
CREATE INDEX IF NOT EXISTS idx_locations_codes ON locations(warehouse_code, location_code);
CREATE INDEX IF NOT EXISTS idx_inventory_balances_part_id ON inventory_balances(part_id);
CREATE INDEX IF NOT EXISTS idx_inventory_balances_location_id ON inventory_balances(location_id);
CREATE INDEX IF NOT EXISTS idx_inventory_transactions_part_id ON inventory_transactions(part_id);
CREATE INDEX IF NOT EXISTS idx_inventory_transactions_location_id ON inventory_transactions(location_id);
CREATE INDEX IF NOT EXISTS idx_inventory_transactions_created_at ON inventory_transactions(created_at);
CREATE INDEX IF NOT EXISTS idx_import_runs_created_at ON import_runs(created_at);
