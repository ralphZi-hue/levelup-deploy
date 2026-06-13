"""FamBank – Leitner-Karteikasten-Logik.

5 Fächer mit wachsenden Wiederholungs-Abständen (Spaced Repetition).
Richtig -> Karte rückt ein Fach vor; falsch -> zurück in Fach 1.
"""
from __future__ import annotations

from datetime import datetime, timedelta

# Fach -> Tage bis zur nächsten Wiederholung
BOX_DAYS = {1: 0, 2: 1, 3: 3, 4: 7, 5: 14}
MAX_BOX = 5

SUBJECT_EMOJI = {
    "englisch": "🇬🇧", "deutsch": "📖", "mathe": "➗", "mathematik": "➗",
    "biologie": "🧬", "bio": "🧬", "chemie": "⚗️", "physik": "🔭",
    "geschichte": "🏛️", "geografie": "🌍", "erdkunde": "🌍",
    "französisch": "🇫🇷", "latein": "🏺", "spanisch": "🇪🇸",
}


def subject_emoji(subject: str) -> str:
    return SUBJECT_EMOJI.get((subject or "").strip().lower(), "📚")


def next_due(box: int, now: datetime = None) -> str:
    now = now or datetime.utcnow()
    days = BOX_DAYS.get(box, 0)
    return (now + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def apply_answer(box: int, correct: bool) -> int:
    """Neues Fach nach einer Antwort."""
    if correct:
        return min(MAX_BOX, (box or 1) + 1)
    return 1


def parse_bulk(text: str):
    """Zerlegt eingefügten Text in (front, back)-Paare.

    Erkennt pro Zeile die Trenner: Tab, '=', ';', '|', ' - ', ' – '.
    Leere Zeilen werden ignoriert.
    """
    pairs = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        front = back = None
        for sep in ("\t", " – ", " - ", "=", ";", "|"):
            if sep in line:
                front, back = line.split(sep, 1)
                break
        if front is None:
            continue  # keine erkennbare Trennung -> überspringen
        front, back = front.strip(), back.strip()
        if front and back:
            pairs.append((front, back))
    return pairs
