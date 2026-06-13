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

CREATE TABLE IF NOT EXISTS evidence (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_id         INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    filename      TEXT    NOT NULL,            -- zufälliger Dateiname in data/uploads/
    mime          TEXT,
    size          INTEGER,
    gps_lat       REAL,                        -- Browser-Geolocation (zuverlässigste Quelle)
    gps_lon       REAL,
    gps_accuracy  REAL,                        -- Genauigkeit in Metern
    exif_lat      REAL,                        -- aus dem Bild ausgelesen (best effort)
    exif_lon      REAL,
    exif_time     TEXT,                        -- DateTimeOriginal aus dem Bild
    client_time   TEXT,                        -- Zeit laut Gerät (manipulierbar)
    server_time   TEXT    NOT NULL DEFAULT (datetime('now')),  -- fälschungssicher (Server setzt sie)
    uploaded_by   INTEGER NOT NULL REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS decks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,                -- z.B. "Unit 5 Vokabeln"
    subject     TEXT    NOT NULL,                -- z.B. Englisch, Mathe, Biologie
    child_id    INTEGER NOT NULL REFERENCES users(id),   -- für welches Kind
    created_by  INTEGER NOT NULL REFERENCES users(id),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id     INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    front       TEXT    NOT NULL,                -- Frage / Vokabel
    back        TEXT    NOT NULL,                -- Antwort / Übersetzung
    box         INTEGER NOT NULL DEFAULT 1,      -- Leitner-Fach 1..5
    due_at      TEXT,                            -- nächste Fälligkeit (ISO); NULL = sofort
    last_result TEXT,                            -- 'correct' / 'wrong'
    reviews     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS test_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id     INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    child_id    INTEGER NOT NULL REFERENCES users(id),
    status      TEXT    NOT NULL DEFAULT 'unlocked'
                        CHECK (status IN ('unlocked','running','passed','failed','expired','cancelled')),
    reward      INTEGER NOT NULL DEFAULT 100,   -- Gutschrift bei Bestehen, in Cent
    pass_pct    INTEGER NOT NULL DEFAULT 80,    -- nötige Trefferquote
    total       INTEGER NOT NULL DEFAULT 0,     -- Anzahl Fragen (beim Start gesetzt)
    correct     INTEGER NOT NULL DEFAULT 0,
    unlocked_by INTEGER REFERENCES users(id),
    unlocked_at TEXT    NOT NULL DEFAULT (datetime('now')),
    started_at  TEXT,
    finished_at TEXT,
    expires_at  TEXT,                           -- bis dahin startbar
    tx_id       INTEGER REFERENCES transactions(id)   -- erzeugte Gutschrift
);

CREATE TABLE IF NOT EXISTS test_answers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES test_sessions(id) ON DELETE CASCADE,
    card_id     INTEGER NOT NULL REFERENCES cards(id),
    given       TEXT,
    is_correct  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS invites (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    token         TEXT    NOT NULL UNIQUE,
    email         TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    username      TEXT    NOT NULL UNIQUE,
    role          TEXT    NOT NULL CHECK (role IN ('admin','child')),
    base_allowance INTEGER NOT NULL DEFAULT 0,
    created_by    INTEGER NOT NULL REFERENCES users(id),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    used_at       TEXT
);

CREATE TABLE IF NOT EXISTS assignments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id      INTEGER NOT NULL REFERENCES users(id),
    title         TEXT    NOT NULL,
    category      TEXT    NOT NULL CHECK (category IN ('schule','haushalt','verhalten')),
    amount        INTEGER NOT NULL,              -- signiert, in Cent (+ Belohnung / - Abzug)
    deadline      TEXT,                          -- YYYY-MM-DD, optional
    requires_proof INTEGER NOT NULL DEFAULT 0,
    note          TEXT,
    status        TEXT    NOT NULL DEFAULT 'open'
                          CHECK (status IN ('open','pending','approved','rejected')),
    tx_id         INTEGER REFERENCES transactions(id),
    created_by    INTEGER NOT NULL REFERENCES users(id),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    completed_at  TEXT,
    decided_by    INTEGER REFERENCES users(id),
    decided_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_tx_child  ON transactions(child_id);
CREATE INDEX IF NOT EXISTS idx_tx_status ON transactions(status);
CREATE INDEX IF NOT EXISTS idx_ev_tx     ON evidence(tx_id);
CREATE INDEX IF NOT EXISTS idx_deck_child ON decks(child_id);
CREATE INDEX IF NOT EXISTS idx_card_deck  ON cards(deck_id);
CREATE INDEX IF NOT EXISTS idx_ts_child   ON test_sessions(child_id);
CREATE INDEX IF NOT EXISTS idx_ta_session ON test_answers(session_id);
CREATE INDEX IF NOT EXISTS idx_invite_token ON invites(token);
CREATE INDEX IF NOT EXISTS idx_assign_child  ON assignments(child_id);
CREATE INDEX IF NOT EXISTS idx_assign_status ON assignments(status);

CREATE TABLE IF NOT EXISTS dinner_config (
    id          INTEGER PRIMARY KEY DEFAULT 1,
    active      INTEGER NOT NULL DEFAULT 0,
    dinner_time TEXT,
    menu        TEXT,
    date        TEXT
);
INSERT OR IGNORE INTO dinner_config(id) VALUES(1);

CREATE TABLE IF NOT EXISTS dinner_slots (
    slot_key TEXT PRIMARY KEY CHECK (slot_key IN ('decken','abdecken','kueche')),
    label    TEXT NOT NULL DEFAULT '',
    sort_ord INTEGER NOT NULL DEFAULT 0,
    amount   INTEGER NOT NULL DEFAULT 50,
    mode     TEXT NOT NULL DEFAULT 'plus' CHECK (mode IN ('plus','minus','both')),
    active   INTEGER NOT NULL DEFAULT 1
);
INSERT OR IGNORE INTO dinner_slots(slot_key, label, sort_ord, amount) VALUES
    ('decken',   'Tisch decken',   1, 50),
    ('abdecken', 'Tisch abdecken', 2, 50),
    ('kueche',   'Küche aufräumen',3, 100);

CREATE TABLE IF NOT EXISTS dinner_claims (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    date         TEXT NOT NULL,
    slot_key     TEXT NOT NULL REFERENCES dinner_slots(slot_key),
    child_id     INTEGER NOT NULL REFERENCES users(id),
    committed_at TEXT NOT NULL DEFAULT (datetime('now')),
    result       TEXT CHECK (result IN ('done','late','missed')),
    tx_id        INTEGER REFERENCES transactions(id),
    UNIQUE(date, slot_key)
);
CREATE INDEX IF NOT EXISTS idx_dinner_date ON dinner_claims(date);

CREATE TABLE IF NOT EXISTS pocket_transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id    INTEGER NOT NULL REFERENCES users(id),
    amount      INTEGER NOT NULL,              -- signiert, in Cent
    type        TEXT    NOT NULL CHECK (type IN ('payout','deposit','withdrawal')),
    note        TEXT,
    created_by  INTEGER NOT NULL REFERENCES users(id),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pocket_child ON pocket_transactions(child_id);
"""


MIGRATIONS = [
    # (table, column, ddl)
    ("users", "cash_legacy", "ALTER TABLE users ADD COLUMN cash_legacy INTEGER NOT NULL DEFAULT 0"),
    ("users", "email", "ALTER TABLE users ADD COLUMN email TEXT"),
    ("users", "iban", "ALTER TABLE users ADD COLUMN iban TEXT"),
    ("rules", "max_uses", "ALTER TABLE rules ADD COLUMN max_uses INTEGER"),
    ("rules", "period", "ALTER TABLE rules ADD COLUMN period TEXT"),
    ("rules", "amount_mode", "ALTER TABLE rules ADD COLUMN amount_mode TEXT NOT NULL DEFAULT 'both'"),
    ("rules", "is_surprise", "ALTER TABLE rules ADD COLUMN is_surprise INTEGER NOT NULL DEFAULT 0"),
    ("rules", "conditions", "ALTER TABLE rules ADD COLUMN conditions TEXT"),
]


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        existing = {}
        for table, _col, _ddl in MIGRATIONS:
            if table not in existing:
                existing[table] = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for table, col, ddl in MIGRATIONS:
            if col not in existing[table]:
                conn.execute(ddl)


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


def rule_uses_this_period(conn: sqlite3.Connection, child_id: int, rule_id: int, period: str) -> int:
    """Anzahl genehmigter/ausstehender Nutzungen einer Regel in der aktuellen Periode."""
    if period == "day":
        where = "AND date(t.created_at) = date('now')"
    elif period == "week":
        where = "AND t.created_at >= datetime('now', '-6 days')"
    elif period == "month":
        where = "AND strftime('%Y-%m', t.created_at) = strftime('%Y-%m', 'now')"
    else:
        return 0
    return conn.execute(
        f"SELECT COUNT(*) AS c FROM transactions t "
        f"WHERE t.child_id = ? AND t.rule_id = ? AND t.status IN ('pending','approved') {where}",
        (child_id, rule_id),
    ).fetchone()["c"]


def pocket_balance(conn: sqlite3.Connection, child_id: int) -> int:
    """Aktueller Taschengeldkonto-Stand in Cent."""
    return conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM pocket_transactions WHERE child_id = ?",
        (child_id,),
    ).fetchone()["s"]


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
