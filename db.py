"""FamBank – Datenbank-Schicht (SQLite).

Alle Geldbeträge werden als ganze Cent (Integer) gespeichert, um
Rundungsfehler mit Fließkomma zu vermeiden.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "fambank.db")


def get_conn() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    username      TEXT    NOT NULL UNIQUE,
    role          TEXT    NOT NULL CHECK (role IN ('admin','child')),
    password_hash TEXT    NOT NULL,
    base_allowance INTEGER NOT NULL DEFAULT 0,   -- Grund-Taschengeld in Cent (pro Auszahlung)
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT    NOT NULL,
    category      TEXT    NOT NULL CHECK (category IN ('schule','haushalt','verhalten')),
    amount        INTEGER NOT NULL,              -- signiert, in Cent (+ Gutschrift / - Belastung)
    requires_proof INTEGER NOT NULL DEFAULT 0,   -- 0/1: Beweisfoto nötig (Phase 2)
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transactions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id      INTEGER NOT NULL REFERENCES users(id),
    rule_id       INTEGER REFERENCES rules(id),
    title         TEXT    NOT NULL,
    amount        INTEGER NOT NULL,              -- signiert, in Cent
    status        TEXT    NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending','approved','rejected')),
    note          TEXT,
    created_by    INTEGER NOT NULL REFERENCES users(id),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    decided_by    INTEGER REFERENCES users(id),
    decided_at    TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_tx_child  ON transactions(child_id);
CREATE INDEX IF NOT EXISTS idx_tx_status ON transactions(status);
"""


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Hilfsfunktionen für Salden
# ---------------------------------------------------------------------------

def child_balance(conn: sqlite3.Connection, child_id: int) -> int:
    """Aktueller (voraussichtlicher) Saldo in Cent = Grundbetrag + bestätigte Buchungen."""
    row = conn.execute("SELECT base_allowance FROM users WHERE id = ?", (child_id,)).fetchone()
    base = row["base_allowance"] if row else 0
    s = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM transactions "
        "WHERE child_id = ? AND status = 'approved'",
        (child_id,),
    ).fetchone()["s"]
    return base + s


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
