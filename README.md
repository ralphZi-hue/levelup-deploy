# Level 🏆

Familien-App für **Taschengeld, Aufgaben, Verhalten und Lernen** – für Ralph, Anja, Julian und Vincent.

> Code-intern und im Ordner (`Apps/FamBank/`) heißt das Projekt weiterhin „FamBank" – die App selbst heißt jetzt **Level**.

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

### Phase 4 – Detailplanung (Stand 2026-06-13, noch offen)

**4.1 Lunch Money pro Woche**
- Neues Feld pro Kind: wöchentlicher Zusatzbetrag, automatisch gutgeschrieben (eigene Transaktion,
  sichtbar im Verlauf – nicht einfach in base_allowance integriert)
- Offen: Betrag pro Kind individuell (Admin setzt im UI)

**4.2 Belege/Auslagen einreichen**
- Kind reicht Foto + freien Betrag + Notiz ein ("Beleg einreichen", eigener Bereich im Dashboard,
  nicht über die feste Regel-Liste)
- Landet als pending Transaktion (Kategorie "Auslagen") im Admin-Bereich mit Foto zur Prüfung
- Bei Genehmigung: Gutschrift, Beleg als "erledigt" markiert
- Offen: Können Eltern den Betrag vor Genehmigung noch anpassen (z.B. Trinkgeld nicht erstattet)?

**4.3 Wissenstest mit Eltern-Live-Bewertung** (ersetzt/erweitert 3b)
- Fortschrittsbalken im Kind-Dashboard: Beherrschungsgrad pro Karteikasten (Metrik offen –
  Anteil Fach 4-5 vs. Ø Fach/5), Motivationstexte bei Annäherung an Schwelle
- Bei Erreichen der Schwelle: Kind kann Test "beantragen" (statt bisher: Eltern schalten frei ohne Antrag)
- Begleiteter Test: Kind tippt Antworten (wie bisher), aber KEINE Auto-Korrektur mehr
- Eltern sehen live (Polling) Frage+Antwort des Kindes parallel und bewerten jede Antwort:
  Falsch / 25% / 50% / 75% / Richtig (nur Eltern-Eingabe)
- Endergebnis = Ø der Eltern-Bewertungen → bestanden: Gutschrift, nicht bestanden: **Abzug**
- Offen: Höhe des Abzugs bei Nichtbestehen (symmetrisch zur Belohnung? fester Betrag? pro Test wählbar?)
- Offen: genaue Mastery-Schwelle/Metrik (s.o.)

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
- `test_sessions` / `test_answers` – verifizierte Tests (Status, Belohnung, Trefferquote, Ergebnisse)
- `settings` – Konfiguration (z.B. Auszahlungstag)

Beweisfotos liegen unter `data/uploads/` (gitignored) und werden nur über die geschützte
Route `/evidence/{id}/image` ausgeliefert – Kinder sehen nur ihre eigenen.

Beträge werden als **ganze Cent** gespeichert (keine Fließkomma-Rundungsfehler).
