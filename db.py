"""SQLite layer for Client Radar."""
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.environ.get(
    "CLIENT_RADAR_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "client_radar.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS licenses (
    key_hash TEXT PRIMARY KEY,
    tier TEXT NOT NULL,
    email TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    keyword_id INTEGER,
    title TEXT DEFAULT '',
    body TEXT DEFAULT '',
    url TEXT DEFAULT '',
    author TEXT DEFAULT '',
    created_utc INTEGER DEFAULT 0,
    score INTEGER DEFAULT 0,
    rationale TEXT DEFAULT '',
    draft TEXT DEFAULT '',
    scored_by TEXT DEFAULT 'heuristic',
    fetched_at TEXT NOT NULL,
    UNIQUE(source, source_id)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);
"""


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def get_setting(key, default=""):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_db()
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def utcnow():
    return datetime.now(timezone.utc).isoformat()
