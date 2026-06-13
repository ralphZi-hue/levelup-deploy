# FamBank 🏦

Familien-App für **Taschengeld, Aufgaben, Verhalten und Lernen** – für Ralph, Anja, Julian und Vincent.

Eltern (Admin) stellen Regeln auf und bestätigen Buchungen; die Kinder sehen ein
Dashboard mit ihrer **voraussichtlichen nächsten Taschengeld-Auszahlung**.

## Stack
- **Python / FastAPI + SQLite + PWA** (installierbar auf dem Handy)
- Keine externen Krypto-Abhängigkeiten (PBKDF2 aus der Standardbibliothek)

## Lokal starten
```bash
# Doppelklick auf start_fambank.command  – oder:
cd Apps/FamBank
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/uvicorn server:app --reload --port 8770
```
Dann im Browser: <http://localhost:8770>

## Accounts (Start-Passwörter – nach erstem Login ändern!)
| Name | Benutzer | Passwort | Rolle |
|------|----------|----------|-------|
| Ralph | `ralph` | `ralph123` | Admin |
| Anja | `anja` | `anja123` | Admin |
| Julian | `julian` | `julian123` | Kind |
| Vincent | `vincent` | `vincent123` | Kind |

> ⚠️ Standard-Passwörter unbedingt ändern, bevor die App online gehostet wird.

## Phasen-Roadmap
- **Phase 1 (jetzt):** Konto + Regelwerk + Genehmigungs-Workflow + Dashboards ✅
- **Phase 2:** Foto-Beweise (Live-Kamera, Server-Zeitstempel, Geodaten/EXIF)
- **Phase 3:** Lernprogramm – Leitner-Karteikasten + verifizierter Test (Eltern-PIN, Prüfungsmodus)
- **Phase 4:** Auszahlungs-Zyklen, Passwort-Self-Service, Hosting (Domain + HTTPS)

## Datenmodell (Kurz)
- `users` – Accounts mit Rolle (`admin`/`child`) und Grund-Taschengeld
- `rules` – Regelkatalog (Titel, Kategorie, Betrag ±, Beweis nötig)
- `transactions` – Buchungen (Ledger), Status `pending`/`approved`/`rejected`
- `settings` – Konfiguration (z.B. Auszahlungstag)

Beträge werden als **ganze Cent** gespeichert (keine Fließkomma-Rundungsfehler).
