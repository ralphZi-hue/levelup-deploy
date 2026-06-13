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
- **Phase 1:** Konto + Regelwerk + Genehmigungs-Workflow + Dashboards ✅
- **Phase 2:** Foto-Beweise (Live-Kamera, Server-Zeitstempel, Geodaten/EXIF) ✅
- **Phase 3a:** Leitner-Karteikasten + freies Üben ✅
- **Phase 3b:** Verifizierter Test (Eltern schalten frei, Prüfungsmodus) → Gutschrift bei Bestehen ✅
- **Phase 4 (nächste):** Auszahlungs-Zyklen, Passwort-Self-Service, Hosting (Domain + HTTPS)

### Lernen (Phase 3a)
Eltern legen pro Kind & Fach Karteikästen an (`/admin/learn`) und füllen Karten einzeln oder per
Bulk-Import (Trenner `=`, `-`, `;`, `|`, Tab). Kinder üben unter `/learn` mit dem **Leitner-System**
(5 Fächer, Abstände 0/1/3/7/14 Tage). Freies Üben gibt **kein** Geld.

### Verifizierter Test (Phase 3b)
Eltern schalten im Admin-Bereich pro Karteikasten einen Test frei (Belohnung wählbar) – **ohne PIN**,
die Freischaltung im eigenen Account ist die Berechtigung. 20-Min-Startfenster. Das Kind tippt die
Antworten im **Prüfungsmodus** mit Countdown ein; die Lösungen liegen **nie im Browser** (Auswertung
server-seitig in `learn.check_answer`) – Schutz gegen Schummeln per KI oder Quelltext. Ab der
Trefferquote (Standard 80 %) gibt es automatisch eine Gutschrift, markiert als „✅ verifiziert".

### Hinweis zu „fälschungssicher" (Phase 2)
Die **Server-Zeit** eines Beweisfotos ist verlässlich (der Server setzt sie). Geräte-Zeit und
Browser-/EXIF-Standort werden zusätzlich gespeichert, sind aber technisch theoretisch manipulierbar
(GPS-Spoofing). Für den Familienalltag ist die Kombination ein starker Beleg, aber **keine**
kryptografische Garantie – das wäre nur mit einer nativen App + Hardware-Attestation möglich.

## Datenmodell (Kurz)
- `users` – Accounts mit Rolle (`admin`/`child`) und Grund-Taschengeld
- `rules` – Regelkatalog (Titel, Kategorie, Betrag ±, Beweis nötig)
- `transactions` – Buchungen (Ledger), Status `pending`/`approved`/`rejected`
- `evidence` – Beweisfotos zu Buchungen (Datei, Server-/Geräte-Zeit, Geo, EXIF)
- `decks` / `cards` – Karteikästen pro Kind & Fach + Leitner-Karten (Fach 1–5, Fälligkeit)
- `settings` – Konfiguration (z.B. Auszahlungstag)

Beweisfotos liegen unter `data/uploads/` (gitignored) und werden nur über die geschützte
Route `/evidence/{id}/image` ausgeliefert – Kinder sehen nur ihre eigenen.

Beträge werden als **ganze Cent** gespeichert (keine Fließkomma-Rundungsfehler).
