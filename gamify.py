"""FamBank – Gamification: Level aus lebenslang verdientem „XP".

XP = Summe aller bestätigten **Gutschriften** (nur positive Beträge). Strafen
senken das Level nicht – das hält die Motivation oben (Level kann nur steigen).
Der aktuelle Kontostand (mit Abzügen) bleibt davon unberührt.
"""
from __future__ import annotations

# (Schwelle in Cent, Name, Emoji)
LEVELS = [
    (0,     "Frischling",   "🐣"),
    (500,   "Entdecker",    "🧭"),
    (1500,  "Sammler",      "🎒"),
    (3000,  "Aufsteiger",   "🚀"),
    (5000,  "Profi",        "⭐"),
    (8000,  "Crack",        "🔥"),
    (12000, "Champion",     "🏆"),
    (17000, "Meister",      "👑"),
    (24000, "Legende",      "🌟"),
    (32000, "Großmeister",  "💎"),
]
STEP_AFTER = 10000  # nach dem letzten Namens-Level: alle 100 € ein weiteres Level


def level_info(xp_cents: int) -> dict:
    xp = max(0, int(xp_cents or 0))
    idx = 0
    for i, (floor, _, _) in enumerate(LEVELS):
        if xp >= floor:
            idx = i
    level = idx + 1
    name, emoji = LEVELS[idx][1], LEVELS[idx][2]
    cur_floor = LEVELS[idx][0]

    if idx + 1 < len(LEVELS):
        next_floor = LEVELS[idx + 1][0]
    else:
        # über das letzte Namens-Level hinaus weiter hochzählen
        over = xp - LEVELS[-1][0]
        extra = over // STEP_AFTER
        level = len(LEVELS) + int(extra)
        cur_floor = LEVELS[-1][0] + int(extra) * STEP_AFTER
        next_floor = cur_floor + STEP_AFTER

    span = next_floor - cur_floor
    into = xp - cur_floor
    pct = int(round(100 * into / span)) if span else 100
    return {
        "level": level, "name": name, "emoji": emoji,
        "xp": xp, "cur_floor": cur_floor, "next_floor": next_floor,
        "into": into, "to_next": max(0, next_floor - xp), "pct": max(0, min(100, pct)),
    }
