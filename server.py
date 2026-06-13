"""FamBank – FastAPI-Server (MVP: Konto + Regelwerk + Genehmigung).

Start:  uvicorn server:app --reload   (oder ./start_fambank.command)
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import sqlite3

import evidence as ev
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
    return templates.TemplateResponse(
        "child.html",
        {
            "request": request, "user": user, "balance": balance,
            "recent": recent, "pending": pending, "rules": rules,
            "payout_day": payout_day, "flash": pop_flash(request),
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
