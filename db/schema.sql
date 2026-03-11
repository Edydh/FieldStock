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

CREATE TABLE IF NOT EXISTS reference_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_name, source_type, source_url)
);

CREATE TABLE IF NOT EXISTS system_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    manufacturer TEXT,
    model_name TEXT NOT NULL,
    normalized_model_name TEXT NOT NULL,
    model_family TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(normalized_model_name)
);

CREATE TABLE IF NOT EXISTS reference_parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    part_number TEXT NOT NULL,
    normalized_part_number TEXT NOT NULL,
    description TEXT,
    manufacturer TEXT,
    product_url TEXT,
    source_title TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, normalized_part_number),
    FOREIGN KEY(source_id) REFERENCES reference_sources(id)
);

CREATE TABLE IF NOT EXISTS reference_part_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference_part_id INTEGER NOT NULL,
    alias_part_number TEXT NOT NULL,
    normalized_alias_part_number TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(reference_part_id, normalized_alias_part_number),
    FOREIGN KEY(reference_part_id) REFERENCES reference_parts(id)
);

CREATE TABLE IF NOT EXISTS system_part_compatibility (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    system_model_id INTEGER NOT NULL,
    reference_part_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    evidence TEXT,
    source_url TEXT,
    confidence REAL NOT NULL DEFAULT 0.75,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(system_model_id, reference_part_id, source_id),
    FOREIGN KEY(system_model_id) REFERENCES system_models(id),
    FOREIGN KEY(reference_part_id) REFERENCES reference_parts(id),
    FOREIGN KEY(source_id) REFERENCES reference_sources(id)
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
CREATE INDEX IF NOT EXISTS idx_system_models_normalized_model_name ON system_models(normalized_model_name);
CREATE INDEX IF NOT EXISTS idx_reference_parts_normalized_part_number ON reference_parts(normalized_part_number);
CREATE INDEX IF NOT EXISTS idx_reference_part_aliases_normalized_alias_part_number ON reference_part_aliases(normalized_alias_part_number);
CREATE INDEX IF NOT EXISTS idx_system_part_compatibility_system_model_id ON system_part_compatibility(system_model_id);
CREATE INDEX IF NOT EXISTS idx_system_part_compatibility_reference_part_id ON system_part_compatibility(reference_part_id);
