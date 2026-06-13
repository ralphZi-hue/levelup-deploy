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
            "SELECT * FROM transactions WHERE child_id = ? ORDER BY id DESC LIMIT 15",
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


@app.post("/claim")
def submit_claim(request: Request, rule_id: int = Form(...), note: str = Form("")):
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
        conn.execute(
            "INSERT INTO transactions(child_id, rule_id, title, amount, status, note, created_by) "
            "VALUES(?,?,?,?,'pending',?,?)",
            (user["id"], rule["id"], rule["title"], rule["amount"], note.strip(), user["id"]),
        )
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
            "SELECT t.*, u.name AS child_name FROM transactions t "
            "JOIN users u ON u.id = t.child_id "
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
        conn.execute(
            "INSERT INTO transactions(child_id, rule_id, title, amount, status, note, "
            "created_by, decided_by, decided_at) "
            "VALUES(?,?,?,?,'approved',?,?,?, datetime('now'))",
            (child_id, rule_id or None, title, amount, note.strip(), user["id"], user["id"]),
        )
    flash(request, "Buchung erfasst. ✅")
    return RedirectResponse("/admin", status_code=303)


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
