"""FamBank – Erstbefüllung: Accounts und ein Start-Regelwerk.

Idempotent: legt nur an, was noch nicht existiert. Standard-Passwörter
sollten nach dem ersten Login geändert werden (Phase 2: Self-Service).
"""
from __future__ import annotations

from auth import hash_password
from db import db, init_db

# (name, username, rolle, passwort, grund-taschengeld in cent)
USERS = [
    ("Ralph", "ralph", "admin", "ralph123", 0),
    ("Anja", "anja", "admin", "anja123", 0),
    ("Julian", "julian", "child", "julian123", 1500),   # 15,00 € Grundbetrag
    ("Vincent", "vincent", "child", "vincent123", 1500),
]

# (titel, kategorie, betrag in cent (+/-), beweis nötig)
RULES = [
    ("Note 1 geschrieben", "schule", 500, 0),
    ("Note 2 geschrieben", "schule", 300, 0),
    ("Note 5 oder 6 geschrieben", "schule", -300, 0),
    ("Vokabeltest bestanden", "schule", 200, 0),
    ("Spülmaschine ausgeräumt", "haushalt", 50, 1),
    ("Tisch abgeräumt", "haushalt", 50, 1),
    ("Essen gekocht", "haushalt", 200, 1),
    ("Zimmer aufgeräumt", "haushalt", 100, 1),
    ("Handtuch liegen gelassen", "verhalten", -100, 1),
    ("Küche nicht aufgeräumt", "verhalten", -100, 1),
    ("Zu spät gekommen", "verhalten", -150, 0),
    ("Pünktlich & zuverlässig", "verhalten", 100, 0),
]


def seed() -> None:
    init_db()
    with db() as conn:
        for name, username, role, pw, base in USERS:
            exists = conn.execute(
                "SELECT 1 FROM users WHERE username = ?", (username,)
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO users(name, username, role, password_hash, base_allowance) "
                    "VALUES(?,?,?,?,?)",
                    (name, username, role, hash_password(pw), base),
                )
                print(f"  + Account: {name} ({username})")

        have_rules = conn.execute("SELECT COUNT(*) AS c FROM rules").fetchone()["c"]
        if have_rules == 0:
            for title, cat, amount, proof in RULES:
                conn.execute(
                    "INSERT INTO rules(title, category, amount, requires_proof) VALUES(?,?,?,?)",
                    (title, cat, amount, proof),
                )
            print(f"  + {len(RULES)} Beispiel-Regeln")

        # Standard-Einstellung: Auszahlungstag im Monat
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('payout_day', '1')"
        )
    print("Seed fertig.")


if __name__ == "__main__":
    seed()
