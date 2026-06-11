"""SQLite schema and connection handling.

Design: change-event storage. A price_events row is written only when a
product's price changes; products carry first_seen/last_seen to distinguish
"price held" from "not observed". Raw API dumps (kept separately, gzipped)
are the re-derivation safety net.
"""

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("data/grocery.db")

CHAINS = ("coles", "woolies", "aldi")

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    chain TEXT NOT NULL CHECK (chain IN ('coles', 'woolies', 'aldi')),
    sku TEXT NOT NULL,
    name TEXT,
    brand TEXT,
    description TEXT,
    quantity REAL,
    unit TEXT,
    is_weighted INTEGER NOT NULL DEFAULT 0,
    category TEXT,
    url TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'scrape',
    UNIQUE (chain, sku)
);

CREATE TABLE IF NOT EXISTS price_events (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    date TEXT NOT NULL,
    price_c INTEGER NOT NULL,
    was_price_c INTEGER,
    is_member_price INTEGER NOT NULL DEFAULT 0,
    store_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'scrape',
    UNIQUE (product_id, date, store_id)
);
CREATE INDEX IF NOT EXISTS idx_price_events_product
    ON price_events (product_id, date);

CREATE TABLE IF NOT EXISTS attribute_events (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    date TEXT NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT
);
CREATE INDEX IF NOT EXISTS idx_attribute_events_product
    ON attribute_events (product_id, date);

CREATE TABLE IF NOT EXISTS match_groups (
    id INTEGER PRIMARY KEY,
    name TEXT,
    match_type TEXT NOT NULL CHECK (match_type IN ('identical', 'equivalent'))
);

CREATE TABLE IF NOT EXISTS match_members (
    group_id INTEGER NOT NULL REFERENCES match_groups(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    method TEXT NOT NULL CHECK (method IN ('auto', 'llm', 'manual')),
    confidence REAL,
    PRIMARY KEY (group_id, product_id)
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id INTEGER PRIMARY KEY,
    chain TEXT NOT NULL,
    date TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    product_count INTEGER,
    status TEXT NOT NULL DEFAULT 'running',
    raw_path TEXT,
    UNIQUE (chain, date)
);
"""


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
