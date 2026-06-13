"""LevelUp – WSGI-Adapter für PythonAnywhere (kostenlose Stufe = nur WSGI, kein ASGI).

In der PythonAnywhere-WSGI-Konfigurationsdatei (Web-Tab -> WSGI configuration file)
den vorhandenen Inhalt löschen und ersetzen durch:

    import sys
    path = "/home/DEINUSERNAME/levelup"   # Pfad zu diesem Ordner anpassen
    if path not in sys.path:
        sys.path.insert(0, path)

    from pythonanywhere_wsgi import application
"""
from __future__ import annotations

from a2wsgi import ASGIMiddleware

from server import app as _asgi_app

# ASGIMiddleware startet einen Hintergrund-Thread mit eigenem Event-Loop.
# uWSGI laedt dieses Modul im Master-Prozess und forkt danach die Worker --
# Threads ueberleben einen fork() nicht. Daher die Instanz erst bei der
# ersten Anfrage (im Worker-Prozess) erzeugen, nicht beim Modul-Import.
_application: ASGIMiddleware | None = None


def application(environ, start_response):
    global _application
    if _application is None:
        _application = ASGIMiddleware(_asgi_app)
    return _application(environ, start_response)
