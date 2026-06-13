"""LevelUp – Mailversand für Einladungen (SMTP).

SMTP-Zugangsdaten liegen in der `settings`-Tabelle (Admin-UI unter /admin/users)
unter den Keys smtp_host, smtp_port, smtp_user, smtp_pass, smtp_from, smtp_tls.
"""
from __future__ import annotations

import smtplib
import sqlite3
from email.message import EmailMessage

from db import get_setting


class MailError(Exception):
    pass


def smtp_configured(conn: sqlite3.Connection) -> bool:
    return bool(get_setting(conn, "smtp_host") and get_setting(conn, "smtp_user"))


def send_invite_email(conn: sqlite3.Connection, to_email: str, name: str, link: str) -> None:
    host = get_setting(conn, "smtp_host")
    port = int(get_setting(conn, "smtp_port", "587") or "587")
    user = get_setting(conn, "smtp_user")
    password = get_setting(conn, "smtp_pass")
    sender = get_setting(conn, "smtp_from", user) or user
    use_tls = get_setting(conn, "smtp_tls", "1") != "0"

    if not host or not user:
        raise MailError("SMTP ist noch nicht eingerichtet (siehe Einstellungen unten).")

    msg = EmailMessage()
    msg["Subject"] = "Einladung zu LevelUp"
    msg["From"] = sender
    msg["To"] = to_email
    msg.set_content(
        f"Hallo {name}!\n\n"
        f"Du wurdest zu LevelUp eingeladen. Klicke auf den folgenden Link, "
        f"um dein Konto zu aktivieren und ein Passwort festzulegen:\n\n"
        f"{link}\n\n"
        f"Der Link ist nur für dich gedacht.\n"
    )

    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            if use_tls:
                smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"Mail konnte nicht gesendet werden: {e}") from e
