"""FamBank – FastAPI-Server (MVP: Konto + Regelwerk + Genehmigung).

Start:  uvicorn server:app --reload   (oder ./start_fambank.command)
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import sqlite3

import evidence as ev
import gamify
import learn
from auth import authenticate, session_secret
from db import BASE_DIR, child_balance, db, get_setting, init_db
from seed import seed

app = FastAPI(title="FamBank")
app.add_middleware(SessionMiddleware, secret_key=session_secret(), max_age=60 * 60 * 24 * 14)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


def euro(cents: Optional[int]) -> str:
    cents = cents or 0
    sign = "-" if cents < 0 else ""
    v = abs(cents)
    return f"{sign}{v // 100},{v % 100:02d} €"


templates.env.filters["euro"] = euro
templates.env.filters["subj_emoji"] = learn.subject_emoji


# ---------------------------------------------------------------------------
# Session-Helfer
# ---------------------------------------------------------------------------

def current_user(request: Request, conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    uid = request.session.get("uid")
    if not uid:
        return None
    return conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def flash(request: Request, msg: str, kind: str = "ok") -> None:
    request.session["flash"] = {"msg": msg, "kind": kind}


def pop_flash(request: Request):
    return request.session.pop("flash", None)


def _f(v: str):
    """'12.34' -> float, '' -> None (defensiv gegen leere Formularfelder)."""
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _xp(conn: sqlite3.Connection, child_id: int) -> int:
    """Lebenslang verdiente XP = Summe aller bestätigten Gutschriften (positive Beträge)."""
    return conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM transactions "
        "WHERE child_id = ? AND status = 'approved' AND amount > 0",
        (child_id,),
    ).fetchone()["s"]


def _weekly_change(conn: sqlite3.Connection, child_id: int) -> int:
    """Netto-Veränderung der letzten 7 Tage (bestätigt), in Cent."""
    return conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM transactions "
        "WHERE child_id = ? AND status = 'approved' "
        "AND COALESCE(decided_at, created_at) >= datetime('now', '-7 days')",
        (child_id,),
    ).fetchone()["s"]


def _trend(cents: int) -> str:
    """Grobe Richtung für den Vergleich – ohne genaue Beträge zu verraten."""
    if cents > 200:
        return "up2"      # stark im Plus
    if cents > 0:
        return "up"
    if cents < -200:
        return "down2"    # stark im Minus
    if cents < 0:
        return "down"
    return "flat"


def attach_evidence(
    conn: sqlite3.Connection, tx_id: int, uploaded_by: int, photo: Optional[UploadFile],
    lat: str = "", lon: str = "", accuracy: str = "", client_time: str = "",
) -> bool:
    """Speichert ein Beweisfoto (falls vorhanden) und verknüpft es mit der Buchung.

    Der Server setzt die maßgebliche Zeit selbst (server_time, DEFAULT in der DB);
    Geräte-Zeit und Geräte-Geo werden zusätzlich, aber als 'nur Hinweis' gespeichert.
    """
    if not photo or not photo.filename:
        return False
    raw = photo.file.read()
    meta = ev.save_upload(raw, photo.content_type or "")
    exif = ev.read_exif(raw)
    conn.execute(
        "INSERT INTO evidence(tx_id, filename, mime, size, gps_lat, gps_lon, gps_accuracy, "
        "exif_lat, exif_lon, exif_time, client_time, uploaded_by) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            tx_id, meta["filename"], meta["mime"], meta["size"],
            _f(lat), _f(lon), _f(accuracy),
            exif.get("exif_lat"), exif.get("exif_lon"), exif.get("exif_time"),
            client_time.strip() or None, uploaded_by,
        ),
    )
    return True


@app.on_event("startup")
def _startup() -> None:
    init_db()
    # Beim allerersten Start automatisch befüllen
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    if n == 0:
        seed()


# ---------------------------------------------------------------------------
# Auth-Routen
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    with db() as conn:
        user = current_user(request, conn)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/admin" if user["role"] == "admin" else "/me", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(
        "login.html", {"request": request, "flash": pop_flash(request)}
    )


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    with db() as conn:
        user = authenticate(conn, username, password)
    if not user:
        flash(request, "Benutzername oder Passwort falsch.", "err")
        return RedirectResponse("/login", status_code=303)
    request.session["uid"] = user["id"]
    return RedirectResponse("/", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------------------
# Kind-Bereich
# ---------------------------------------------------------------------------

@app.get("/me", response_class=HTMLResponse)
def child_dashboard(request: Request):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "child":
            return RedirectResponse("/", status_code=303)
        balance = child_balance(conn, user["id"])
        recent = conn.execute(
            "SELECT t.*, (SELECT e.id FROM evidence e WHERE e.tx_id = t.id LIMIT 1) AS evidence_id "
            "FROM transactions t WHERE t.child_id = ? ORDER BY t.id DESC LIMIT 15",
            (user["id"],),
        ).fetchall()
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM transactions WHERE child_id = ? AND status = 'pending'",
            (user["id"],),
        ).fetchone()["c"]
        rules = conn.execute(
            "SELECT * FROM rules WHERE active = 1 ORDER BY category, amount DESC"
        ).fetchall()
        payout_day = get_setting(conn, "payout_day", "1")

        level = gamify.level_info(_xp(conn, user["id"]))
        week = _weekly_change(conn, user["id"])
        # Geschwister-Vergleich: nur Level + Trend, keine genauen Beträge
        siblings = []
        for c in conn.execute(
            "SELECT id, name FROM users WHERE role = 'child' AND id != ? ORDER BY name",
            (user["id"],),
        ).fetchall():
            li = gamify.level_info(_xp(conn, c["id"]))
            siblings.append({"name": c["name"], "level": li, "trend": _trend(_weekly_change(conn, c["id"]))})
    return templates.TemplateResponse(
        "child.html",
        {
            "request": request, "user": user, "balance": balance,
            "recent": recent, "pending": pending, "rules": rules,
            "payout_day": payout_day, "flash": pop_flash(request),
            "level": level, "week": week, "week_trend": _trend(week), "siblings": siblings,
        },
    )


@app.get("/claim/new", response_class=HTMLResponse)
def claim_new(request: Request, rule_id: int):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "child":
            return RedirectResponse("/", status_code=303)
        rule = conn.execute(
            "SELECT * FROM rules WHERE id = ? AND active = 1", (rule_id,)
        ).fetchone()
    if not rule:
        flash(request, "Regel nicht gefunden.", "err")
        return RedirectResponse("/me", status_code=303)
    return templates.TemplateResponse(
        "claim_new.html", {"request": request, "user": user, "rule": rule}
    )


@app.post("/claim")
def submit_claim(
    request: Request,
    rule_id: int = Form(...),
    note: str = Form(""),
    photo: Optional[UploadFile] = File(None),
    lat: str = Form(""),
    lon: str = Form(""),
    accuracy: str = Form(""),
    client_time: str = Form(""),
):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "child":
            return RedirectResponse("/", status_code=303)
        rule = conn.execute(
            "SELECT * FROM rules WHERE id = ? AND active = 1", (rule_id,)
        ).fetchone()
        if not rule:
            flash(request, "Regel nicht gefunden.", "err")
            return RedirectResponse("/me", status_code=303)
        has_photo = bool(photo and photo.filename)
        if rule["requires_proof"] and not has_photo:
            flash(request, "Für diese Aufgabe ist ein Beweisfoto nötig. 📷", "err")
            return RedirectResponse(f"/claim/new?rule_id={rule_id}", status_code=303)
        cur = conn.execute(
            "INSERT INTO transactions(child_id, rule_id, title, amount, status, note, created_by) "
            "VALUES(?,?,?,?,'pending',?,?)",
            (user["id"], rule["id"], rule["title"], rule["amount"], note.strip(), user["id"]),
        )
        try:
            attach_evidence(conn, cur.lastrowid, user["id"], photo, lat, lon, accuracy, client_time)
        except ev.UploadError as e:
            flash(request, f"Foto-Problem: {e}", "err")
            return RedirectResponse(f"/claim/new?rule_id={rule_id}", status_code=303)
    flash(request, "Antrag gestellt – wartet auf Bestätigung durch Mama/Papa. ✅")
    return RedirectResponse("/me", status_code=303)


# ---------------------------------------------------------------------------
# Admin-Bereich
# ---------------------------------------------------------------------------

@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        children = conn.execute(
            "SELECT * FROM users WHERE role = 'child' ORDER BY name"
        ).fetchall()
        kids = []
        for c in children:
            kids.append({"row": c, "balance": child_balance(conn, c["id"])})
        pending = conn.execute(
            "SELECT t.*, u.name AS child_name, "
            "(SELECT e.id FROM evidence e WHERE e.tx_id = t.id LIMIT 1) AS evidence_id "
            "FROM transactions t JOIN users u ON u.id = t.child_id "
            "WHERE t.status = 'pending' ORDER BY t.id ASC"
        ).fetchall()
        rules = conn.execute(
            "SELECT * FROM rules WHERE active = 1 ORDER BY category, amount DESC"
        ).fetchall()
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request, "user": user, "kids": kids,
            "pending": pending, "rules": rules, "flash": pop_flash(request),
        },
    )


@app.post("/admin/tx/{tx_id}/decide")
def decide_tx(request: Request, tx_id: int, action: str = Form(...)):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        status = "approved" if action == "approve" else "rejected"
        conn.execute(
            "UPDATE transactions SET status = ?, decided_by = ?, decided_at = datetime('now') "
            "WHERE id = ? AND status = 'pending'",
            (status, user["id"], tx_id),
        )
    flash(request, "Bestätigt. ✅" if action == "approve" else "Abgelehnt.")
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/book")
def admin_book(
    request: Request,
    child_id: int = Form(...),
    rule_id: int = Form(0),
    custom_title: str = Form(""),
    custom_amount: str = Form(""),
    note: str = Form(""),
    photo: Optional[UploadFile] = File(None),
    lat: str = Form(""),
    lon: str = Form(""),
    accuracy: str = Form(""),
    client_time: str = Form(""),
):
    """Direktbuchung durch Eltern – sofort gültig (z.B. Vergehen oder Sonderfall)."""
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        title, amount = custom_title.strip(), None
        if rule_id:
            rule = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
            if rule:
                title, amount = rule["title"], rule["amount"]
        if amount is None:
            try:
                # Eingabe in Euro, z.B. "-1,50" oder "2"
                amount = int(round(float(custom_amount.replace(",", ".")) * 100))
            except ValueError:
                flash(request, "Betrag ungültig.", "err")
                return RedirectResponse("/admin", status_code=303)
        if not title:
            flash(request, "Titel/Regel fehlt.", "err")
            return RedirectResponse("/admin", status_code=303)
        cur = conn.execute(
            "INSERT INTO transactions(child_id, rule_id, title, amount, status, note, "
            "created_by, decided_by, decided_at) "
            "VALUES(?,?,?,?,'approved',?,?,?, datetime('now'))",
            (child_id, rule_id or None, title, amount, note.strip(), user["id"], user["id"]),
        )
        try:
            attach_evidence(conn, cur.lastrowid, user["id"], photo, lat, lon, accuracy, client_time)
        except ev.UploadError as e:
            flash(request, f"Buchung gespeichert, aber Foto-Problem: {e}", "err")
            return RedirectResponse("/admin", status_code=303)
    flash(request, "Buchung erfasst. ✅")
    return RedirectResponse("/admin", status_code=303)


# ---------------------------------------------------------------------------
# Beweisfotos ausliefern (geschützt) + Metadaten
# ---------------------------------------------------------------------------

def _evidence_if_allowed(request: Request, conn: sqlite3.Connection, ev_id: int):
    user = current_user(request, conn)
    if not user:
        return None, None
    row = conn.execute(
        "SELECT e.*, t.child_id FROM evidence e JOIN transactions t ON t.id = e.tx_id "
        "WHERE e.id = ?",
        (ev_id,),
    ).fetchone()
    if not row:
        return user, None
    # Admins dürfen alles; Kinder nur ihre eigenen Beweise
    if user["role"] != "admin" and user["id"] != row["child_id"]:
        return user, None
    return user, row


@app.get("/evidence/{ev_id}/image")
def evidence_image(request: Request, ev_id: int):
    with db() as conn:
        user, row = _evidence_if_allowed(request, conn, ev_id)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not row:
        return HTMLResponse("Nicht gefunden.", status_code=404)
    path = ev.path_for(row["filename"])
    if not os.path.exists(path):
        return HTMLResponse("Datei fehlt.", status_code=404)
    return FileResponse(path, media_type=row["mime"] or "application/octet-stream")


@app.get("/evidence/{ev_id}", response_class=HTMLResponse)
def evidence_detail(request: Request, ev_id: int):
    with db() as conn:
        user, row = _evidence_if_allowed(request, conn, ev_id)
        tx = None
        if row:
            tx = conn.execute(
                "SELECT t.*, u.name AS child_name FROM transactions t "
                "JOIN users u ON u.id = t.child_id WHERE t.id = ?",
                (row["tx_id"],),
            ).fetchone()
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not row:
        return HTMLResponse("Nicht gefunden.", status_code=404)
    return templates.TemplateResponse(
        "evidence.html", {"request": request, "user": user, "ev": row, "tx": tx}
    )


@app.get("/admin/rules", response_class=HTMLResponse)
def admin_rules(request: Request):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        rules = conn.execute(
            "SELECT * FROM rules ORDER BY active DESC, category, amount DESC"
        ).fetchall()
    return templates.TemplateResponse(
        "rules.html",
        {"request": request, "user": user, "rules": rules, "flash": pop_flash(request)},
    )


@app.post("/admin/rules/add")
def add_rule(
    request: Request,
    title: str = Form(...),
    category: str = Form(...),
    amount: str = Form(...),
    requires_proof: str = Form("0"),
):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        try:
            cents = int(round(float(amount.replace(",", ".")) * 100))
        except ValueError:
            flash(request, "Betrag ungültig (z.B. 0,50 oder -1).", "err")
            return RedirectResponse("/admin/rules", status_code=303)
        conn.execute(
            "INSERT INTO rules(title, category, amount, requires_proof) VALUES(?,?,?,?)",
            (title.strip(), category, cents, 1 if requires_proof == "1" else 0),
        )
    flash(request, "Regel hinzugefügt. ✅")
    return RedirectResponse("/admin/rules", status_code=303)


@app.post("/admin/rules/{rule_id}/toggle")
def toggle_rule(request: Request, rule_id: int):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        conn.execute("UPDATE rules SET active = 1 - active WHERE id = ?", (rule_id,))
    return RedirectResponse("/admin/rules", status_code=303)


# ---------------------------------------------------------------------------
# Lernen – Leitner-Karteikasten (Kind: üben)
# ---------------------------------------------------------------------------

def _deck_stats(conn: sqlite3.Connection, deck_id: int) -> dict:
    total = conn.execute("SELECT COUNT(*) AS c FROM cards WHERE deck_id = ?", (deck_id,)).fetchone()["c"]
    due = conn.execute(
        "SELECT COUNT(*) AS c FROM cards WHERE deck_id = ? AND (due_at IS NULL OR due_at <= datetime('now'))",
        (deck_id,),
    ).fetchone()["c"]
    learned = conn.execute(
        "SELECT COUNT(*) AS c FROM cards WHERE deck_id = ? AND box >= 5", (deck_id,)
    ).fetchone()["c"]
    return {"total": total, "due": due, "learned": learned}


def _expire_stale_tests(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE test_sessions SET status = 'expired' "
        "WHERE status = 'unlocked' AND expires_at IS NOT NULL AND expires_at < datetime('now')"
    )


def _active_test(conn: sqlite3.Connection, deck_id: int, child_id: int):
    return conn.execute(
        "SELECT * FROM test_sessions WHERE deck_id = ? AND child_id = ? "
        "AND status IN ('unlocked','running') ORDER BY id DESC LIMIT 1",
        (deck_id, child_id),
    ).fetchone()


@app.get("/learn", response_class=HTMLResponse)
def learn_home(request: Request):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "child":
            return RedirectResponse("/", status_code=303)
        _expire_stale_tests(conn)
        rows = conn.execute(
            "SELECT * FROM decks WHERE child_id = ? ORDER BY subject, title", (user["id"],)
        ).fetchall()
        decks = [
            {"row": d, "stats": _deck_stats(conn, d["id"]), "test": _active_test(conn, d["id"], user["id"])}
            for d in rows
        ]
    return templates.TemplateResponse(
        "learn_child.html",
        {"request": request, "user": user, "decks": decks, "flash": pop_flash(request)},
    )


@app.get("/learn/{deck_id}", response_class=HTMLResponse)
def learn_practice(request: Request, deck_id: int):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "child":
            return RedirectResponse("/", status_code=303)
        deck = conn.execute(
            "SELECT * FROM decks WHERE id = ? AND child_id = ?", (deck_id, user["id"])
        ).fetchone()
        if not deck:
            flash(request, "Karteikasten nicht gefunden.", "err")
            return RedirectResponse("/learn", status_code=303)
        cards = conn.execute(
            "SELECT id, front, back, box FROM cards WHERE deck_id = ? "
            "AND (due_at IS NULL OR due_at <= datetime('now')) ORDER BY box, RANDOM() LIMIT 30",
            (deck_id,),
        ).fetchall()
    return templates.TemplateResponse(
        "practice.html",
        {"request": request, "user": user, "deck": deck,
         "cards": [dict(c) for c in cards]},
    )


@app.post("/learn/card/{card_id}/answer")
def learn_answer(request: Request, card_id: int, correct: str = Form(...)):
    """Wird per fetch() aus der Übungs-Seite aufgerufen; aktualisiert das Leitner-Fach."""
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "child":
            return JSONResponse({"ok": False}, status_code=403)
        row = conn.execute(
            "SELECT c.* FROM cards c JOIN decks d ON d.id = c.deck_id "
            "WHERE c.id = ? AND d.child_id = ?",
            (card_id, user["id"]),
        ).fetchone()
        if not row:
            return JSONResponse({"ok": False}, status_code=404)
        is_correct = correct in ("1", "true", "yes")
        new_box = learn.apply_answer(row["box"], is_correct)
        conn.execute(
            "UPDATE cards SET box = ?, due_at = ?, last_result = ?, reviews = reviews + 1 WHERE id = ?",
            (new_box, learn.next_due(new_box), "correct" if is_correct else "wrong", card_id),
        )
    return JSONResponse({"ok": True, "box": new_box})


# ---------------------------------------------------------------------------
# Lernen – Verwaltung (Admin/Eltern)
# ---------------------------------------------------------------------------

@app.get("/admin/learn", response_class=HTMLResponse)
def admin_learn(request: Request):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        children = conn.execute("SELECT * FROM users WHERE role = 'child' ORDER BY name").fetchall()
        _expire_stale_tests(conn)
        rows = conn.execute(
            "SELECT d.*, u.name AS child_name FROM decks d JOIN users u ON u.id = d.child_id "
            "ORDER BY u.name, d.subject, d.title"
        ).fetchall()
        decks = []
        for d in rows:
            active = _active_test(conn, d["id"], d["child_id"])
            last = conn.execute(
                "SELECT * FROM test_sessions WHERE deck_id = ? AND status IN ('passed','failed') "
                "ORDER BY id DESC LIMIT 1", (d["id"],)
            ).fetchone()
            decks.append({"row": d, "stats": _deck_stats(conn, d["id"]), "active": active, "last": last})
    return templates.TemplateResponse(
        "admin_learn.html",
        {"request": request, "user": user, "children": children, "decks": decks,
         "flash": pop_flash(request)},
    )


@app.post("/admin/learn/deck/add")
def admin_deck_add(
    request: Request, title: str = Form(...), subject: str = Form(...), child_id: int = Form(...)
):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        cur = conn.execute(
            "INSERT INTO decks(title, subject, child_id, created_by) VALUES(?,?,?,?)",
            (title.strip(), subject.strip(), child_id, user["id"]),
        )
        new_id = cur.lastrowid
    flash(request, "Karteikasten angelegt. ✅")
    return RedirectResponse(f"/admin/learn/deck/{new_id}", status_code=303)


@app.get("/admin/learn/deck/{deck_id}", response_class=HTMLResponse)
def admin_deck(request: Request, deck_id: int):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        deck = conn.execute(
            "SELECT d.*, u.name AS child_name FROM decks d JOIN users u ON u.id = d.child_id "
            "WHERE d.id = ?", (deck_id,)
        ).fetchone()
        if not deck:
            flash(request, "Karteikasten nicht gefunden.", "err")
            return RedirectResponse("/admin/learn", status_code=303)
        cards = conn.execute(
            "SELECT * FROM cards WHERE deck_id = ? ORDER BY id", (deck_id,)
        ).fetchall()
    return templates.TemplateResponse(
        "admin_deck.html",
        {"request": request, "user": user, "deck": deck, "cards": cards,
         "flash": pop_flash(request)},
    )


@app.post("/admin/learn/deck/{deck_id}/cards/add")
def admin_cards_add(
    request: Request, deck_id: int,
    front: str = Form(""), back: str = Form(""), bulk: str = Form(""),
):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        deck = conn.execute("SELECT id FROM decks WHERE id = ?", (deck_id,)).fetchone()
        if not deck:
            return RedirectResponse("/admin/learn", status_code=303)
        added = 0
        pairs = learn.parse_bulk(bulk)
        if front.strip() and back.strip():
            pairs.append((front.strip(), back.strip()))
        for f, b in pairs:
            conn.execute("INSERT INTO cards(deck_id, front, back) VALUES(?,?,?)", (deck_id, f, b))
            added += 1
    flash(request, f"{added} Karte(n) hinzugefügt. ✅" if added else "Nichts erkannt – Format prüfen.",
          "ok" if added else "err")
    return RedirectResponse(f"/admin/learn/deck/{deck_id}", status_code=303)


@app.post("/admin/learn/card/{card_id}/delete")
def admin_card_delete(request: Request, card_id: int, deck_id: int = Form(...)):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
    return RedirectResponse(f"/admin/learn/deck/{deck_id}", status_code=303)


@app.post("/admin/learn/deck/{deck_id}/delete")
def admin_deck_delete(request: Request, deck_id: int):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        conn.execute("DELETE FROM cards WHERE deck_id = ?", (deck_id,))
        conn.execute("DELETE FROM decks WHERE id = ?", (deck_id,))
    flash(request, "Karteikasten gelöscht.")
    return RedirectResponse("/admin/learn", status_code=303)


# ---------------------------------------------------------------------------
# Verifizierter Test (Phase 3b) – Freischaltung durch Eltern, ohne PIN
# ---------------------------------------------------------------------------

@app.post("/admin/learn/deck/{deck_id}/unlock_test")
def admin_unlock_test(request: Request, deck_id: int, reward: str = Form(""), pass_pct: str = Form("")):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        deck = conn.execute("SELECT * FROM decks WHERE id = ?", (deck_id,)).fetchone()
        if not deck:
            return RedirectResponse("/admin/learn", status_code=303)
        ncards = conn.execute("SELECT COUNT(*) AS c FROM cards WHERE deck_id = ?", (deck_id,)).fetchone()["c"]
        if ncards == 0:
            flash(request, "Dieser Karteikasten hat noch keine Karten.", "err")
            return RedirectResponse("/admin/learn", status_code=303)
        try:
            reward_c = int(round(float((reward or "1").replace(",", ".")) * 100))
        except ValueError:
            reward_c = learn.TEST_DEFAULT_REWARD
        try:
            pp = max(1, min(100, int(pass_pct or learn.TEST_DEFAULT_PASS)))
        except ValueError:
            pp = learn.TEST_DEFAULT_PASS
        # frühere offene Tests dieses Decks/Kindes verwerfen
        conn.execute(
            "UPDATE test_sessions SET status = 'cancelled' "
            "WHERE deck_id = ? AND child_id = ? AND status IN ('unlocked','running')",
            (deck_id, deck["child_id"]),
        )
        conn.execute(
            "INSERT INTO test_sessions(deck_id, child_id, status, reward, pass_pct, unlocked_by, expires_at) "
            "VALUES(?,?,'unlocked',?,?,?, datetime('now', ?))",
            (deck_id, deck["child_id"], reward_c, pp, user["id"],
             f"+{learn.TEST_START_WINDOW_MIN} minutes"),
        )
    flash(request, f"Test freigeschaltet ({learn.TEST_START_WINDOW_MIN} Min startbar). 🎓")
    return RedirectResponse("/admin/learn", status_code=303)


def _load_test(conn: sqlite3.Connection, session_id: int, child_id: int):
    return conn.execute(
        "SELECT * FROM test_sessions WHERE id = ? AND child_id = ?", (session_id, child_id)
    ).fetchone()


@app.get("/test/{session_id}", response_class=HTMLResponse)
def test_run(request: Request, session_id: int):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "child":
            return RedirectResponse("/", status_code=303)
        _expire_stale_tests(conn)
        s = _load_test(conn, session_id, user["id"])
        if not s or s["status"] in ("passed", "failed", "expired", "cancelled"):
            flash(request, "Dieser Test ist nicht (mehr) verfügbar.", "err")
            return RedirectResponse("/learn", status_code=303)
        deck = conn.execute("SELECT * FROM decks WHERE id = ?", (s["deck_id"],)).fetchone()
        if s["status"] == "unlocked":
            # Test starten: Fragen auswählen (nur Vorderseiten ans Kind!), Zustand setzen
            cards = conn.execute(
                "SELECT id, front FROM cards WHERE deck_id = ? ORDER BY RANDOM() LIMIT ?",
                (s["deck_id"], learn.TEST_MAX_QUESTIONS),
            ).fetchall()
            conn.execute(
                "UPDATE test_sessions SET status = 'running', started_at = datetime('now'), "
                "total = ?, correct = 0 WHERE id = ?",
                (len(cards), session_id),
            )
            conn.execute("DELETE FROM test_answers WHERE session_id = ?", (session_id,))
            questions = [dict(c) for c in cards]
            total = len(cards)
        else:  # bereits 'running' – Fragen erneut anzeigen (Backenddaten bleiben)
            cards = conn.execute(
                "SELECT id, front FROM cards WHERE deck_id = ? ORDER BY RANDOM() LIMIT ?",
                (s["deck_id"], s["total"] or learn.TEST_MAX_QUESTIONS),
            ).fetchall()
            questions = [dict(c) for c in cards]
            total = s["total"] or len(cards)
        secs = max(30, total * learn.TEST_SECONDS_PER_Q)
    return templates.TemplateResponse(
        "test.html",
        {"request": request, "user": user, "deck": deck, "session": s,
         "questions": questions, "seconds": secs},
    )


@app.post("/test/{session_id}/answer")
def test_answer(request: Request, session_id: int, card_id: int = Form(...), answer: str = Form("")):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "child":
            return JSONResponse({"ok": False}, status_code=403)
        s = _load_test(conn, session_id, user["id"])
        if not s or s["status"] != "running":
            return JSONResponse({"ok": False}, status_code=409)
        card = conn.execute(
            "SELECT * FROM cards WHERE id = ? AND deck_id = ?", (card_id, s["deck_id"])
        ).fetchone()
        if not card:
            return JSONResponse({"ok": False}, status_code=404)
        # nur einmal pro Karte werten
        already = conn.execute(
            "SELECT 1 FROM test_answers WHERE session_id = ? AND card_id = ?", (session_id, card_id)
        ).fetchone()
        if already:
            return JSONResponse({"ok": True, "duplicate": True})
        ok = learn.check_answer(answer, card["back"])
        conn.execute(
            "INSERT INTO test_answers(session_id, card_id, given, is_correct) VALUES(?,?,?,?)",
            (session_id, card_id, answer.strip(), 1 if ok else 0),
        )
        if ok:
            conn.execute("UPDATE test_sessions SET correct = correct + 1 WHERE id = ?", (session_id,))
    # Die richtige Antwort wird NICHT zurückgegeben (es ist ein Test)
    return JSONResponse({"ok": True, "correct": ok})


@app.post("/test/{session_id}/finish")
def test_finish(request: Request, session_id: int):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "child":
            return JSONResponse({"ok": False}, status_code=403)
        s = _load_test(conn, session_id, user["id"])
        if not s:
            return JSONResponse({"ok": False}, status_code=404)
        if s["status"] in ("passed", "failed"):
            pct = round(100 * s["correct"] / s["total"]) if s["total"] else 0
            return JSONResponse({"ok": True, "passed": s["status"] == "passed",
                                 "correct": s["correct"], "total": s["total"],
                                 "pct": pct, "reward": s["reward"]})
        if s["status"] != "running":
            return JSONResponse({"ok": False}, status_code=409)
        total = s["total"] or 0
        correct = conn.execute(
            "SELECT COUNT(*) AS c FROM test_answers WHERE session_id = ? AND is_correct = 1",
            (session_id,),
        ).fetchone()["c"]
        pct = round(100 * correct / total) if total else 0
        passed = pct >= s["pass_pct"]
        deck = conn.execute("SELECT * FROM decks WHERE id = ?", (s["deck_id"],)).fetchone()
        tx_id = None
        if passed and s["reward"] > 0:
            cur = conn.execute(
                "INSERT INTO transactions(child_id, rule_id, title, amount, status, note, "
                "created_by, decided_by, decided_at) "
                "VALUES(?,?,?,?,'approved',?,?,?, datetime('now'))",
                (s["child_id"], None, f"✅ Test bestanden: {deck['title']}", s["reward"],
                 f"Verifiziert · {correct}/{total} richtig ({pct}%)",
                 s["unlocked_by"] or user["id"], s["unlocked_by"] or user["id"]),
            )
            tx_id = cur.lastrowid
        conn.execute(
            "UPDATE test_sessions SET status = ?, correct = ?, finished_at = datetime('now'), tx_id = ? "
            "WHERE id = ?",
            ("passed" if passed else "failed", correct, tx_id, session_id),
        )
    return JSONResponse({"ok": True, "passed": passed, "correct": correct, "total": total,
                         "pct": pct, "reward": s["reward"] if passed else 0})
