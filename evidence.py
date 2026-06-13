"""FamBank – Beweisfotos: Speicherung + Best-Effort-EXIF-Auslesen.

Bewusst ohne Pillow/exifread: ein kompakter, defensiver JPEG-EXIF-Parser
(Standardbibliothek). Schlägt das Parsen fehl, wird einfach nichts zurückgegeben –
die zuverlässige Quelle für den Standort ist ohnehin die Browser-Geolocation,
die getrennt erfasst und vom Server mit einem Zeitstempel versehen wird.
"""
from __future__ import annotations

import os
import secrets
import struct
from typing import Optional

from db import DATA_DIR

UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
MAX_BYTES = 15 * 1024 * 1024  # 15 MB

# erlaubte Bildtypen -> Dateiendung
ALLOWED = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
}


class UploadError(Exception):
    pass


def save_upload(raw: bytes, mime: str) -> dict:
    """Speichert die Bilddaten unter einem zufälligen Namen und liefert Metadaten."""
    mime = (mime or "").lower().split(";")[0].strip()
    ext = ALLOWED.get(mime)
    if not ext:
        raise UploadError(f"Dateityp nicht erlaubt: {mime or 'unbekannt'}")
    if not raw:
        raise UploadError("Leere Datei.")
    if len(raw) > MAX_BYTES:
        raise UploadError("Datei zu groß (max. 15 MB).")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    name = f"{secrets.token_hex(16)}.{ext}"
    path = os.path.join(UPLOAD_DIR, name)
    with open(path, "wb") as f:
        f.write(raw)
    return {"filename": name, "mime": mime, "size": len(raw)}


def path_for(filename: str) -> str:
    return os.path.join(UPLOAD_DIR, filename)


# ---------------------------------------------------------------------------
# Minimaler EXIF-Parser (JPEG, APP1)
# ---------------------------------------------------------------------------

def read_exif(raw: bytes) -> dict:
    out: dict = {}
    try:
        if raw[:2] != b"\xff\xd8":  # kein JPEG -> kein EXIF
            return out
        app1 = _find_app1(raw)
        if not app1:
            return out
        bo = "<" if app1[:2] == b"II" else ">"
        ifd0_off = struct.unpack(bo + "I", app1[4:8])[0]
        ifd0 = _read_ifd(app1, ifd0_off, bo)

        if 0x8769 in ifd0:  # Exif-SubIFD
            exif = _read_ifd(app1, _ptr(ifd0[0x8769], bo), bo)
            if 0x9003 in exif:  # DateTimeOriginal (ASCII)
                typ, cnt, val = exif[0x9003]
                off = struct.unpack(bo + "I", val)[0]
                s = app1[off:off + cnt].split(b"\x00")[0].decode("ascii", "ignore")
                if s:
                    out["exif_time"] = s

        if 0x8825 in ifd0:  # GPS-IFD
            gps = _read_ifd(app1, _ptr(ifd0[0x8825], bo), bo)
            ll = _gps_to_decimal(app1, gps, bo)
            if ll:
                out["exif_lat"], out["exif_lon"] = ll
    except Exception:
        return out
    return out


def _find_app1(raw: bytes) -> Optional[bytes]:
    i = 2
    n = len(raw)
    while i < n - 3 and raw[i] == 0xFF:
        marker = raw[i + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker == 0xDA:  # Start of Scan -> Bilddaten, Schluss
            break
        seg_len = struct.unpack(">H", raw[i + 2:i + 4])[0]
        seg = raw[i + 4:i + 2 + seg_len]
        if marker == 0xE1 and seg[:6] == b"Exif\x00\x00":
            return seg[6:]
        i += 2 + seg_len
    return None


def _read_ifd(app1: bytes, off: int, bo: str) -> dict:
    entries: dict = {}
    if off <= 0 or off + 2 > len(app1):
        return entries
    count = struct.unpack(bo + "H", app1[off:off + 2])[0]
    for k in range(count):
        e = off + 2 + k * 12
        if e + 12 > len(app1):
            break
        tag = struct.unpack(bo + "H", app1[e:e + 2])[0]
        typ = struct.unpack(bo + "H", app1[e + 2:e + 4])[0]
        cnt = struct.unpack(bo + "I", app1[e + 4:e + 8])[0]
        val = app1[e + 8:e + 12]
        entries[tag] = (typ, cnt, val)
    return entries


def _ptr(entry, bo: str) -> int:
    return struct.unpack(bo + "I", entry[2])[0]


def _rational(app1: bytes, bo: str, off: int) -> float:
    num = struct.unpack(bo + "I", app1[off:off + 4])[0]
    den = struct.unpack(bo + "I", app1[off + 4:off + 8])[0]
    return num / den if den else 0.0


def _gps_to_decimal(app1: bytes, gps: dict, bo: str):
    try:
        if 2 not in gps or 4 not in gps:
            return None
        def dms(entry):
            off = struct.unpack(bo + "I", entry[2])[0]
            return (_rational(app1, bo, off)
                    + _rational(app1, bo, off + 8) / 60
                    + _rational(app1, bo, off + 16) / 3600)
        lat = dms(gps[2])
        lon = dms(gps[4])
        if gps.get(1, (0, 0, b"N"))[2][:1] == b"S":
            lat = -lat
        if gps.get(3, (0, 0, b"E"))[2][:1] == b"W":
            lon = -lon
        return round(lat, 6), round(lon, 6)
    except Exception:
        return None
