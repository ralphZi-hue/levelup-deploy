# LevelUp 🏆

Familien-App für **Taschengeld, Aufgaben, Verhalten und Lernen** – für Ralph, Anja, Julian und Vincent.

> Code-intern und im Ordner (`Apps/FamBank/`) heißt das Projekt weiterhin „FamBank" – die App selbst heißt jetzt **LevelUp**.

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

### Phase 4 – Detailplanung (Stand 2026-06-13)

**4.1 Lunch Money pro Schultag**
- Neue Einstellung pro Kind: fester Betrag pro Schultag (Mo–Fr), automatisch gutgeschrieben als
  eigene, sichtbare Transaktion ("Lunch Money") – nicht in base_allowance integriert
- Nur an Schultagen: **Berliner Schulferienkalender** wird hinterlegt, an Ferientagen + Wochenende
  entfällt die Gutschrift
- Default-Werte (änderbar im Admin-UI): Julian 4 € + freitags zusätzlich 8 € (Gitarrenunterricht,
  eigene Buchung "Gitarrenunterricht"), Vincent 4 €. Alle Beträge frei pro Kind & Wochentag editierbar.

**4.2 Belege/Auslagen einreichen**
- Kind reicht Foto + freien Betrag + Notiz ein ("Beleg einreichen", eigener Bereich im Dashboard,
  nicht über die feste Regel-Liste)
- Landet als pending Transaktion (Kategorie "Auslagen") im Admin-Bereich mit Foto zur Prüfung
- Eltern können den eingereichten Betrag vor der Genehmigung anpassen (z.B. Trinkgeld nicht erstattet)
- Bei Genehmigung: Gutschrift (ggf. angepasster Betrag), Beleg als "erledigt" markiert

**4.4 Kontostand-Berichtigung (Auszahlung)**
- Nach realer Überweisung/Bargeldauszahlung: Admin löst "Auszahlung" aus
- Erzeugt eine Ausgleichs-Transaktion, die den laufenden Saldo auf **0 zurücksetzt**
  (Auszahlung = kompletter aktueller Betrag), neue Abrechnungsperiode beginnt
- Gilt zunächst für Julian (hat bereits ein Konto)

**4.5 Vincent Bargeld-Altbestand**
- Vincent hat noch kein Konto-Guthaben in der App, aber ein bestehendes Bargeld-Taschengeld
- Wird als separate zweite Anzeige "Bargeld-Altbestand" neben seinem App-Saldo dargestellt
  (nicht in den laufenden Saldo eingerechnet)
- Startwert wird von Ralph vorgegeben, später im Admin-Bereich frei änderbar/abbuchbar

**4.3 Wissenstest mit Eltern-Live-Bewertung** (ersetzt/erweitert 3b)
- Fortschrittsbalken im Kind-Dashboard: Beherrschungsgrad pro Karteikasten = **Anteil der Karten
  in Fach 4-5** (Standard-Schwelle 80 %, wie bei Phase 3b), Motivationstexte bei Annäherung an die Schwelle
- Bei Erreichen der Schwelle: Kind kann den Test "beantragen" (statt bisher: Eltern schalten frei ohne Antrag)
- Eltern wählen beim Freischalten **Belohnung UND Abzug** (Abzug wird als fester Betrag vorgeschlagen,
  ist aber änderbar)
- Begleiteter Test: Kind tippt Antworten (wie bisher), aber KEINE Auto-Korrektur mehr
- Eltern sehen live (Polling) Frage+Antwort des Kindes parallel und bewerten jede Antwort:
  Falsch / 25% / 50% / 75% / Richtig (nur Eltern-Eingabe)
- Endergebnis = Ø der Eltern-Bewertungen → **bestanden ab 90 % (Schulnote 1–2)**: Gutschrift (Belohnung);
  nicht bestanden: Abzug

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

### Benutzerverwaltung & Einladungen
Unter `/admin/users` können Admins neue Benutzer (Kind oder Admin) per E-Mail einladen. Admin gibt
Name, Benutzername, E-Mail, Rolle und (bei Kindern) Grund-Taschengeld vor; der Versand erfolgt per
SMTP (Zugangsdaten, z.B. Gmail-App-Passwort, werden auf derselben Seite hinterlegt). Die eingeladene
Person öffnet den Link `/register/{token}` und legt nur ihr eigenes Passwort fest – Name/Benutzername
sind vorgegeben. Einladungen sind einmalig nutzbar, können erneut versendet oder zurückgezogen werden.

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
