#!/bin/bash
# FamBank starten (lokal). Doppelklick im Finder genügt.
cd "$(dirname "$0")" || exit 1

# Virtuelle Umgebung anlegen, falls noch nicht vorhanden
if [ ! -d ".venv" ]; then
  echo "Richte virtuelle Umgebung ein..."
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip >/dev/null
  ./.venv/bin/pip install -r requirements.txt
fi

echo ""
echo "FamBank läuft gleich unter:  http://localhost:8770"
echo "(Zum Beenden dieses Fenster schließen oder Strg+C drücken)"
echo ""

sleep 1
open http://localhost:8770
exec ./.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8770
