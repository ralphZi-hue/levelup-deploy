"""FamBank – FastAPI-Server (MVP: Konto + Regelwerk + Genehmigung).

Start:  uvicorn server:app --reload   (oder ./start_fambank.command)
"""
from __future__ import annotations

import os
import secrets
from datetime import date
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
import mailer
from auth import authenticate, hash_password, session_secret
from db import BASE_DIR, child_balance, pocket_balance, pocket_txs, rule_uses_this_period, db, get_setting, init_db, set_setting
from seed import seed

_MONTH_NAMES = [
    "Januar","Februar","März","April","Mai","Juni",
    "Juli","August","September","Oktober","November","Dezember",
]

app = FastAPI(title="LevelUp")
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


def _dinner_data(conn: sqlite3.Connection, today: str) -> tuple:
    """Gibt (config, slots, claims_by_slot) für heute zurück."""
    config = conn.execute("SELECT * FROM dinner_config WHERE id = 1").fetchone()
    slots = conn.execute("SELECT * FROM dinner_slots ORDER BY sort_ord").fetchall()
    claims = conn.execute(
        "SELECT dc.*, u.name AS child_name FROM dinner_claims dc "
        "JOIN users u ON u.id = dc.child_id WHERE dc.date = ?",
        (today,),
    ).fetchall()
    return config, slots, {c["slot_key"]: c for c in claims}


def _book_tx(conn: sqlite3.Connection, child_id: int, title: str, amount: int,
             note: str, admin_id: int) -> int:
    """Erstellt eine sofort genehmigte Transaktion und gibt die ID zurück."""
    cur = conn.execute(
        "INSERT INTO transactions(child_id, title, amount, status, note, "
        "created_by, decided_by, decided_at) VALUES(?,?,?,'approved',?,?,?,datetime('now'))",
        (child_id, title, amount, note, admin_id, admin_id),
    )
    return cur.lastrowid


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
        request, "login.html", {"request": request, "flash": pop_flash(request)}
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
        rules_raw = conn.execute(
            "SELECT * FROM rules WHERE active = 1 ORDER BY category, amount DESC"
        ).fetchall()
        rule_uses = {
            r["id"]: rule_uses_this_period(conn, user["id"], r["id"], r["period"])
            for r in rules_raw if r["max_uses"] and r["period"]
        }
        rules = rules_raw
        tasks = conn.execute(
            "SELECT * FROM assignments WHERE child_id = ? AND status IN ('open','pending') "
            "ORDER BY (deadline IS NULL), deadline ASC, id ASC",
            (user["id"],),
        ).fetchall()
        payout_day = get_setting(conn, "payout_day", "1")
        pocket = pocket_balance(conn, user["id"])
        p_txs = pocket_txs(conn, user["id"])
        today_str = date.today().isoformat()
        dinner_cfg, dinner_slots, dinner_claims_map = _dinner_data(conn, today_str)

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
        request, "child.html",
        {
            "request": request, "user": user, "balance": balance,
            "recent": recent, "pending": pending, "rules": rules, "tasks": tasks,
            "today": today_str,
            "payout_day": payout_day, "pocket": pocket, "pocket_txs": p_txs, "flash": pop_flash(request),
            "level": level, "week": week, "week_trend": _trend(week), "siblings": siblings,
            "rule_uses": rule_uses,
            "dinner_cfg": dinner_cfg, "dinner_slots": dinner_slots,
            "dinner_claims_map": dinner_claims_map,
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
        request, "claim_new.html", {"request": request, "user": user, "rule": rule}
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
        if rule["max_uses"] and rule["period"]:
            uses = rule_uses_this_period(conn, user["id"], rule["id"], rule["period"])
            if uses >= rule["max_uses"]:
                flash(request, f"Limit für diese Aufgabe erreicht ({rule['max_uses']}× pro {rule['period']}). ⛔", "err")
                return RedirectResponse("/me", status_code=303)
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


@app.get("/tasks/{task_id}/complete", response_class=HTMLResponse)
def task_complete_new(request: Request, task_id: int):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "child":
            return RedirectResponse("/", status_code=303)
        task = conn.execute(
            "SELECT * FROM assignments WHERE id = ? AND child_id = ? AND status = 'open'",
            (task_id, user["id"]),
        ).fetchone()
    if not task:
        flash(request, "Aufgabe nicht gefunden.", "err")
        return RedirectResponse("/me", status_code=303)
    return templates.TemplateResponse(
        request, "task_complete.html", {"request": request, "user": user, "task": task}
    )


@app.post("/tasks/{task_id}/complete")
def task_complete_submit(
    request: Request,
    task_id: int,
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
        task = conn.execute(
            "SELECT * FROM assignments WHERE id = ? AND child_id = ? AND status = 'open'",
            (task_id, user["id"]),
        ).fetchone()
        if not task:
            flash(request, "Aufgabe nicht gefunden.", "err")
            return RedirectResponse("/me", status_code=303)
        has_photo = bool(photo and photo.filename)
        if task["requires_proof"] and not has_photo:
            flash(request, "Für diese Aufgabe ist ein Beweisfoto nötig. 📷", "err")
            return RedirectResponse(f"/tasks/{task_id}/complete", status_code=303)
        cur = conn.execute(
            "INSERT INTO transactions(child_id, title, amount, status, note, created_by) "
            "VALUES(?,?,?,'pending',?,?)",
            (user["id"], task["title"], task["amount"], note.strip(), user["id"]),
        )
        try:
            attach_evidence(conn, cur.lastrowid, user["id"], photo, lat, lon, accuracy, client_time)
        except ev.UploadError as e:
            flash(request, f"Foto-Problem: {e}", "err")
            return RedirectResponse(f"/tasks/{task_id}/complete", status_code=303)
        conn.execute(
            "UPDATE assignments SET status = 'pending', tx_id = ?, completed_at = datetime('now') WHERE id = ?",
            (cur.lastrowid, task_id),
        )
    flash(request, "Als erledigt markiert – wartet auf Bestätigung durch Mama/Papa. ✅")
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
        today = date.today()
        today_str = today.isoformat()
        current_month = f"{_MONTH_NAMES[today.month - 1]} {today.year}"
        dinner_cfg, dinner_slots, dinner_claims_map = _dinner_data(conn, today_str)
        kids = []
        for c in children:
            kids.append({
                "row": c,
                "balance": child_balance(conn, c["id"]),
                "pocket": pocket_balance(conn, c["id"]),
            })
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
        request, "admin.html",
        {
            "request": request, "user": user, "kids": kids,
            "pending": pending, "rules": rules,
            "current_month": current_month, "flash": pop_flash(request),
            "dinner_cfg": dinner_cfg, "dinner_slots": dinner_slots,
            "dinner_claims_map": dinner_claims_map, "today": today_str,
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
        # Hängt eine Aufgabe an dieser Buchung? Bei Bestätigung -> erledigt,
        # bei Ablehnung -> zurück auf "offen" (Kind kann es erneut versuchen).
        assign_status = "approved" if action == "approve" else "open"
        conn.execute(
            "UPDATE assignments SET status = ?, decided_by = ?, decided_at = datetime('now') "
            "WHERE tx_id = ? AND status = 'pending'",
            (assign_status, user["id"], tx_id),
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


@app.post("/admin/payout")
def admin_payout(
    request: Request,
    child_id: int = Form(...),
    method: str = Form("bank"),
    iban: str = Form(""),
    verwendungszweck: str = Form(""),
    pocket_amount: str = Form(""),
):
    """Auszahlung erfassen – entweder als Banküberweisung oder ans Taschengeldkonto."""
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        balance = child_balance(conn, child_id)
        if balance <= 0:
            flash(request, "Saldo ist 0 oder negativ – keine Auszahlung möglich.")
            return RedirectResponse("/admin", status_code=303)

        if method == "pocket" and pocket_amount.strip():
            try:
                transfer = int(round(float(pocket_amount.replace(",", ".")) * 100))
            except ValueError:
                flash(request, "Betrag ungültig.", "err")
                return RedirectResponse("/admin", status_code=303)
            if transfer <= 0 or transfer > balance:
                flash(request, "Betrag muss zwischen 0,01 € und dem Saldo liegen.", "err")
                return RedirectResponse("/admin", status_code=303)
        else:
            transfer = balance

        if iban.strip():
            conn.execute("UPDATE users SET iban = ? WHERE id = ?", (iban.strip(), child_id))

        if method == "pocket":
            note = f"Übertrag aufs Taschengeldkonto · {transfer/100:.2f} €"
        else:
            zweck = verwendungszweck.strip() or "Saldo ausgezahlt"
            note = f"Überweisung · {zweck}"

        conn.execute(
            "INSERT INTO transactions(child_id, title, amount, status, note, "
            "created_by, decided_by, decided_at) "
            "VALUES(?,'Auszahlung',?,'approved',?,?,?,datetime('now'))",
            (child_id, -transfer, note, user["id"], user["id"]),
        )
        if method == "pocket":
            conn.execute(
                "INSERT INTO pocket_transactions(child_id, amount, type, note, created_by) "
                "VALUES(?,?,'payout',?,?)",
                (child_id, transfer, note, user["id"]),
            )
            flash(request, f"{transfer/100:.2f} € aufs Taschengeldkonto übertragen. ✅")
        else:
            flash(request, "Banküberweisung notiert – Saldo zurückgesetzt. ✅")
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/pocket/book")
def admin_pocket_book(
    request: Request,
    child_id: int = Form(...),
    type: str = Form(...),
    amount: str = Form(...),
    note: str = Form(""),
):
    """Manuelle Einzahlung oder Abhebung auf dem Taschengeldkonto."""
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        if type not in ("deposit", "withdrawal"):
            flash(request, "Ungültiger Typ.", "err")
            return RedirectResponse("/admin", status_code=303)
        try:
            cents = int(round(float(amount.replace(",", ".")) * 100))
        except ValueError:
            flash(request, "Betrag ungültig.", "err")
            return RedirectResponse("/admin", status_code=303)
        if cents <= 0:
            flash(request, "Betrag muss größer als 0 sein.", "err")
            return RedirectResponse("/admin", status_code=303)
        signed = cents if type == "deposit" else -cents
        conn.execute(
            "INSERT INTO pocket_transactions(child_id, amount, type, note, created_by) "
            "VALUES(?,?,?,?,?)",
            (child_id, signed, type, note.strip() or None, user["id"]),
        )
    label = "Einzahlung" if type == "deposit" else "Abhebung"
    flash(request, f"{label} auf Taschengeldkonto gebucht. ✅")
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/cash_legacy")
def admin_cash_legacy(request: Request, child_id: int = Form(...), amount: str = Form(...)):
    """Bargeld-Altbestand (z.B. Vincents bisheriges Taschengeld) setzen/ändern."""
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        try:
            cents = int(round(float(amount.replace(",", ".")) * 100))
        except ValueError:
            flash(request, "Betrag ungültig.", "err")
            return RedirectResponse("/admin", status_code=303)
        conn.execute("UPDATE users SET cash_legacy = ? WHERE id = ?", (cents, child_id))
    flash(request, "Bargeld-Altbestand aktualisiert. ✅")
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
        request, "evidence.html", {"request": request, "user": user, "ev": row, "tx": tx}
    )


# ---------------------------------------------------------------------------
# Abendessen (Dinner)
# ---------------------------------------------------------------------------

@app.post("/admin/dinner/config")
def admin_dinner_config(
    request: Request,
    active: str = Form("0"),
    dinner_time: str = Form(""),
    menu: str = Form(""),
    slot_decken_active: str = Form("0"),
    slot_decken_amount: str = Form(""),
    slot_decken_mode: str = Form("plus"),
    slot_abdecken_active: str = Form("0"),
    slot_abdecken_amount: str = Form(""),
    slot_abdecken_mode: str = Form("plus"),
    slot_kueche_active: str = Form("0"),
    slot_kueche_amount: str = Form(""),
    slot_kueche_mode: str = Form("plus"),
):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        today_str = date.today().isoformat()
        conn.execute(
            "UPDATE dinner_config SET active=?, dinner_time=?, menu=?, date=? WHERE id=1",
            (1 if active == "1" else 0, dinner_time.strip() or None,
             menu.strip() or None, today_str),
        )
        for key, amount_s, mode, act_s in [
            ("decken",   slot_decken_amount,   slot_decken_mode,   slot_decken_active),
            ("abdecken", slot_abdecken_amount, slot_abdecken_mode, slot_abdecken_active),
            ("kueche",   slot_kueche_amount,   slot_kueche_mode,   slot_kueche_active),
        ]:
            try:
                cents = int(round(float(amount_s.replace(",", ".")) * 100)) if amount_s.strip() else None
            except ValueError:
                cents = None
            updates = ["active = ?"]
            vals: list = [1 if act_s == "1" else 0]
            if cents is not None:
                updates.append("amount = ?")
                vals.append(cents)
            if mode in ("plus", "minus", "both"):
                updates.append("mode = ?")
                vals.append(mode)
            vals.append(key)
            conn.execute(f"UPDATE dinner_slots SET {', '.join(updates)} WHERE slot_key=?", vals)
    flash(request, "Abendessen gespeichert. ✅")
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/dinner/{claim_id}/settle")
def admin_dinner_settle(request: Request, claim_id: int, result: str = Form(...)):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        if result not in ("done", "late", "missed"):
            flash(request, "Ungültiges Ergebnis.", "err")
            return RedirectResponse("/admin", status_code=303)
        claim = conn.execute(
            "SELECT dc.*, ds.amount, ds.mode, ds.label, u.name AS child_name "
            "FROM dinner_claims dc "
            "JOIN dinner_slots ds ON ds.slot_key = dc.slot_key "
            "JOIN users u ON u.id = dc.child_id "
            "WHERE dc.id = ? AND dc.result IS NULL",
            (claim_id,),
        ).fetchone()
        if not claim:
            flash(request, "Nicht gefunden oder bereits abgerechnet.", "err")
            return RedirectResponse("/admin", status_code=303)
        mode, amount = claim["mode"], claim["amount"]
        tx_id = None
        if result == "done" and mode in ("plus", "both"):
            tx_id = _book_tx(conn, claim["child_id"],
                             f"🍽️ {claim['label']}",
                             amount,
                             f"Abendessen · pünktlich erledigt ✅",
                             user["id"])
        elif result in ("late", "missed") and mode in ("minus", "both"):
            symbol = "⏰" if result == "late" else "❌"
            tx_id = _book_tx(conn, claim["child_id"],
                             f"🍽️ {claim['label']}",
                             -amount,
                             f"Abendessen · {'verspätet' if result=='late' else 'nicht erledigt'} {symbol}",
                             user["id"])
        conn.execute("UPDATE dinner_claims SET result=?, tx_id=? WHERE id=?",
                     (result, tx_id, claim_id))
    flash(request, "Abgerechnet. ✅")
    return RedirectResponse("/admin", status_code=303)


@app.post("/dinner/commit")
def dinner_commit(request: Request, slot_key: str = Form(...)):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "child":
            return RedirectResponse("/", status_code=303)
        cfg = conn.execute("SELECT * FROM dinner_config WHERE id=1").fetchone()
        today_str = date.today().isoformat()
        if not cfg or not cfg["active"] or cfg["date"] != today_str:
            flash(request, "Kein Abendessen heute geplant.", "err")
            return RedirectResponse("/me", status_code=303)
        slot = conn.execute(
            "SELECT * FROM dinner_slots WHERE slot_key=? AND active=1", (slot_key,)
        ).fetchone()
        if not slot:
            flash(request, "Aufgabe nicht verfügbar.", "err")
            return RedirectResponse("/me", status_code=303)
        try:
            conn.execute(
                "INSERT INTO dinner_claims(date, slot_key, child_id) VALUES(?,?,?)",
                (today_str, slot_key, user["id"]),
            )
        except Exception:
            flash(request, "Diese Aufgabe ist bereits vergeben.", "err")
            return RedirectResponse("/me", status_code=303)
    flash(request, f"Angemeldet: {slot['label']} ✅")
    return RedirectResponse("/me", status_code=303)


@app.post("/dinner/cancel")
def dinner_cancel(request: Request, slot_key: str = Form(...)):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "child":
            return RedirectResponse("/", status_code=303)
        today_str = date.today().isoformat()
        conn.execute(
            "DELETE FROM dinner_claims WHERE date=? AND slot_key=? AND child_id=? AND result IS NULL",
            (today_str, slot_key, user["id"]),
        )
    flash(request, "Anmeldung zurückgezogen.")
    return RedirectResponse("/me", status_code=303)


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
        request, "rules.html",
        {"request": request, "user": user, "rules": rules, "flash": pop_flash(request)},
    )


@app.post("/admin/rules/add")
def add_rule(
    request: Request,
    title: str = Form(...),
    category: str = Form(...),
    amount: str = Form(...),
    requires_proof: str = Form("0"),
    amount_mode: str = Form("both"),
    is_surprise: str = Form("0"),
    max_uses: str = Form(""),
    period: str = Form(""),
    conditions: str = Form(""),
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
        try:
            max_uses_int = int(max_uses) if max_uses.strip() else None
        except ValueError:
            max_uses_int = None
        conn.execute(
            "INSERT INTO rules(title, category, amount, requires_proof, "
            "amount_mode, is_surprise, max_uses, period, conditions) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                title.strip(), category, cents,
                1 if requires_proof == "1" else 0,
                amount_mode if amount_mode in ("plus", "minus", "both") else "both",
                1 if is_surprise == "1" else 0,
                max_uses_int,
                period if period in ("day", "week", "month") else None,
                conditions.strip() or None,
            ),
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
# Aufgaben (Assignments)
# ---------------------------------------------------------------------------

@app.get("/admin/tasks", response_class=HTMLResponse)
def admin_tasks(request: Request):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        children = conn.execute(
            "SELECT * FROM users WHERE role = 'child' ORDER BY name"
        ).fetchall()
        assignments = conn.execute(
            "SELECT a.*, u.name AS child_name, "
            "(SELECT e.id FROM evidence e WHERE e.tx_id = a.tx_id LIMIT 1) AS evidence_id "
            "FROM assignments a JOIN users u ON u.id = a.child_id "
            "ORDER BY CASE a.status WHEN 'open' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END, "
            "(a.deadline IS NULL), a.deadline, a.id DESC"
        ).fetchall()
    return templates.TemplateResponse(
        request, "admin_tasks.html",
        {
            "request": request, "user": user, "children": children,
            "assignments": assignments, "today": date.today().isoformat(),
            "flash": pop_flash(request),
        },
    )


@app.post("/admin/tasks/add")
def add_task(
    request: Request,
    child_id: int = Form(...),
    title: str = Form(...),
    category: str = Form(...),
    amount: str = Form(...),
    deadline: str = Form(""),
    requires_proof: str = Form("0"),
    note: str = Form(""),
):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        try:
            cents = int(round(float(amount.replace(",", ".")) * 100))
        except ValueError:
            flash(request, "Betrag ungültig (z.B. 0,50 oder -1).", "err")
            return RedirectResponse("/admin/tasks", status_code=303)
        conn.execute(
            "INSERT INTO assignments(child_id, title, category, amount, deadline, requires_proof, note, created_by) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                child_id, title.strip(), category, cents, deadline.strip() or None,
                1 if requires_proof == "1" else 0, note.strip() or None, user["id"],
            ),
        )
    flash(request, "Aufgabe erstellt. ✅")
    return RedirectResponse("/admin/tasks", status_code=303)


@app.post("/admin/tasks/{task_id}/delete")
def delete_task(request: Request, task_id: int):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        conn.execute("DELETE FROM assignments WHERE id = ? AND status = 'open'", (task_id,))
    flash(request, "Aufgabe gelöscht.")
    return RedirectResponse("/admin/tasks", status_code=303)


# ---------------------------------------------------------------------------
# Benutzerverwaltung & Einladungen
# ---------------------------------------------------------------------------

def _invite_link(request: Request, token: str) -> str:
    return str(request.base_url).rstrip("/") + f"/register/{token}"


@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        users = conn.execute("SELECT * FROM users ORDER BY role, name").fetchall()
        invites = conn.execute(
            "SELECT * FROM invites WHERE used_at IS NULL ORDER BY created_at DESC"
        ).fetchall()
        smtp = {
            "host": get_setting(conn, "smtp_host"),
            "port": get_setting(conn, "smtp_port", "587"),
            "user": get_setting(conn, "smtp_user"),
            "from": get_setting(conn, "smtp_from"),
            "tls": get_setting(conn, "smtp_tls", "1"),
            "configured": mailer.smtp_configured(conn),
        }
    return templates.TemplateResponse(
        request, "admin_users.html",
        {
            "request": request, "user": user, "users": users, "invites": invites,
            "smtp": smtp, "flash": pop_flash(request),
        },
    )


@app.post("/admin/users/invite")
def admin_invite(
    request: Request,
    name: str = Form(...),
    username: str = Form(...),
    email: str = Form(...),
    role: str = Form(...),
    base_allowance: str = Form("0"),
):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)

        name, username, email = name.strip(), username.strip().lower(), email.strip()
        if role not in ("admin", "child"):
            flash(request, "Ungültige Rolle.", "err")
            return RedirectResponse("/admin/users", status_code=303)
        if not name or not username or not email:
            flash(request, "Name, Benutzername und E-Mail sind Pflicht.", "err")
            return RedirectResponse("/admin/users", status_code=303)
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            flash(request, "Benutzername ist bereits vergeben.", "err")
            return RedirectResponse("/admin/users", status_code=303)
        if conn.execute(
            "SELECT 1 FROM invites WHERE username = ? AND used_at IS NULL", (username,)
        ).fetchone():
            flash(request, "Für diesen Benutzernamen gibt es bereits eine offene Einladung.", "err")
            return RedirectResponse("/admin/users", status_code=303)
        try:
            base_cents = int(round(float(base_allowance.replace(",", ".")) * 100)) if role == "child" else 0
        except ValueError:
            flash(request, "Grund-Taschengeld ungültig.", "err")
            return RedirectResponse("/admin/users", status_code=303)

        if not mailer.smtp_configured(conn):
            flash(request, "Bitte zuerst SMTP-Zugangsdaten unten eintragen.", "err")
            return RedirectResponse("/admin/users", status_code=303)

        token = secrets.token_urlsafe(24)
        conn.execute(
            "INSERT INTO invites(token, email, name, username, role, base_allowance, created_by) "
            "VALUES(?,?,?,?,?,?,?)",
            (token, email, name, username, role, base_cents, user["id"]),
        )
        try:
            mailer.send_invite_email(conn, email, name, _invite_link(request, token))
        except mailer.MailError as e:
            flash(request, f"Einladung gespeichert, aber {e}", "err")
            return RedirectResponse("/admin/users", status_code=303)
    flash(request, f"Einladung an {email} verschickt. ✅")
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/invite/{invite_id}/resend")
def admin_invite_resend(request: Request, invite_id: int):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        inv = conn.execute(
            "SELECT * FROM invites WHERE id = ? AND used_at IS NULL", (invite_id,)
        ).fetchone()
        if not inv:
            flash(request, "Einladung nicht gefunden.", "err")
            return RedirectResponse("/admin/users", status_code=303)
        try:
            mailer.send_invite_email(conn, inv["email"], inv["name"], _invite_link(request, inv["token"]))
        except mailer.MailError as e:
            flash(request, str(e), "err")
            return RedirectResponse("/admin/users", status_code=303)
    flash(request, f"Einladung erneut an {inv['email']} verschickt. ✅")
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/invite/{invite_id}/cancel")
def admin_invite_cancel(request: Request, invite_id: int):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        conn.execute("DELETE FROM invites WHERE id = ? AND used_at IS NULL", (invite_id,))
    flash(request, "Einladung zurückgezogen.")
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/delete")
def admin_user_delete(request: Request, user_id: int):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        if user["id"] == user_id:
            flash(request, "Du kannst dich nicht selbst löschen.", "err")
            return RedirectResponse("/admin/users", status_code=303)
        target = conn.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            flash(request, "Benutzer nicht gefunden.", "err")
            return RedirectResponse("/admin/users", status_code=303)
        name = target["name"]
        conn.execute("DELETE FROM transactions WHERE child_id = ?", (user_id,))
        conn.execute("DELETE FROM claims WHERE child_id = ?", (user_id,))
        conn.execute("DELETE FROM dinner_claims WHERE child_id = ?", (user_id,))
        conn.execute("DELETE FROM pocket_transactions WHERE child_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    flash(request, f"{name} wurde gelöscht.")
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/reset-password")
def admin_user_reset_password(
    request: Request, user_id: int,
    new_password: str = Form(...),
):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        if len(new_password) < 4:
            flash(request, "Passwort muss mindestens 4 Zeichen haben.", "err")
            return RedirectResponse("/admin/users", status_code=303)
        target = conn.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            flash(request, "Benutzer nicht gefunden.", "err")
            return RedirectResponse("/admin/users", status_code=303)
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(new_password), user_id),
        )
    flash(request, f"Passwort für {target['name']} wurde zurückgesetzt. ✅")
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/smtp")
def admin_smtp_settings(
    request: Request,
    smtp_host: str = Form(""),
    smtp_port: str = Form("587"),
    smtp_user: str = Form(""),
    smtp_pass: str = Form(""),
    smtp_from: str = Form(""),
    smtp_tls: str = Form("1"),
):
    with db() as conn:
        user = current_user(request, conn)
        if not user or user["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        set_setting(conn, "smtp_host", smtp_host.strip())
        set_setting(conn, "smtp_port", smtp_port.strip() or "587")
        set_setting(conn, "smtp_user", smtp_user.strip())
        if smtp_pass:
            set_setting(conn, "smtp_pass", smtp_pass)
        set_setting(conn, "smtp_from", smtp_from.strip())
        set_setting(conn, "smtp_tls", "1" if smtp_tls == "1" else "0")
    flash(request, "SMTP-Einstellungen gespeichert. ✅")
    return RedirectResponse("/admin/users", status_code=303)


# ---------------------------------------------------------------------------
# Registrierung über Einladungslink
# ---------------------------------------------------------------------------

@app.get("/register/{token}", response_class=HTMLResponse)
def register_form(request: Request, token: str):
    with db() as conn:
        inv = conn.execute(
            "SELECT * FROM invites WHERE token = ? AND used_at IS NULL", (token,)
        ).fetchone()
    if not inv:
        flash(request, "Diese Einladung ist ungültig oder wurde bereits verwendet.", "err")
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request, "register.html", {"request": request, "invite": inv, "flash": pop_flash(request)}
    )


@app.post("/register/{token}")
def register_submit(
    request: Request, token: str,
    password: str = Form(...), password2: str = Form(...),
):
    with db() as conn:
        inv = conn.execute(
            "SELECT * FROM invites WHERE token = ? AND used_at IS NULL", (token,)
        ).fetchone()
        if not inv:
            flash(request, "Diese Einladung ist ungültig oder wurde bereits verwendet.", "err")
            return RedirectResponse("/login", status_code=303)
        if len(password) < 6:
            flash(request, "Passwort muss mindestens 6 Zeichen haben.", "err")
            return RedirectResponse(f"/register/{token}", status_code=303)
        if password != password2:
            flash(request, "Passwörter stimmen nicht überein.", "err")
            return RedirectResponse(f"/register/{token}", status_code=303)
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (inv["username"],)).fetchone():
            flash(request, "Dieser Benutzername ist inzwischen vergeben. Bitte Admin kontaktieren.", "err")
            return RedirectResponse("/login", status_code=303)

        cur = conn.execute(
            "INSERT INTO users(name, username, role, password_hash, base_allowance, email) "
            "VALUES(?,?,?,?,?,?)",
            (inv["name"], inv["username"], inv["role"], hash_password(password),
             inv["base_allowance"], inv["email"]),
        )
        conn.execute("UPDATE invites SET used_at = datetime('now') WHERE id = ?", (inv["id"],))
        request.session["uid"] = cur.lastrowid
    return RedirectResponse("/", status_code=303)


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
        request, "learn_child.html",
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
        request, "practice.html",
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
        request, "admin_learn.html",
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
        request, "admin_deck.html",
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
        request, "test.html",
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
