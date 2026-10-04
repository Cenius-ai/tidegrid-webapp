#!/usr/bin/env bash
#
# Tidegrid setup: install the test runner, install the console script, check the
# import, and seed a demo station file. It finishes by EXITING - it never starts
# a server, because Tidegrid is a command-line tool with no server to start.
#
# Run with:  bash install.sh
#
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

echo '==> Checking the Python interpreter (3.9 or newer)'
"$PYTHON" - <<'CHECK'
import sys

if sys.version_info < (3, 9):
    raise SystemExit(f"Python 3.9+ is required, found {sys.version.split()[0]}")
print("python", sys.version.split()[0])
CHECK

echo '==> Installing dependencies declared in requirements.txt'
if "$PYTHON" -m pip install --disable-pip-version-check --quiet --upgrade pip setuptools wheel \
    && "$PYTHON" -m pip install --disable-pip-version-check --quiet -r requirements.txt; then
    echo 'dependencies installed (the tool itself needs only the standard library)'
else
    echo 'NOTE: pip could not reach a package index; continuing because Tidegrid'
    echo '      imports nothing outside the standard library.'
fi

echo '==> Installing the tidegrid console script (optional convenience)'
if "$PYTHON" -m pip install --disable-pip-version-check --quiet --no-build-isolation -e .; then
    echo "console script installed: $(command -v tidegrid || echo tidegrid)"
else
    echo 'NOTE: editable install skipped; run the tool as: python3 tidegrid.py ...'
fi

echo '==> Self-check: import the module and exercise the catalog'
"$PYTHON" -c 'import tidegrid; print("tidegrid", tidegrid.VERSION, "-", len(tidegrid.CATALOG), "catalog constituents")'
"$PYTHON" tidegrid.py --version >/dev/null

echo '==> Seeding the demo station file (idempotent)'
if [ -f station.json ]; then
    echo 'station.json already present - leaving it alone'
else
    "$PYTHON" tidegrid.py init --file station.json --name 'Port Haven' --datum MLLW \
        --z0 2.35 --units m --tz-offset -300
    "$PYTHON" tidegrid.py add M2 --file station.json --amplitude 3.72 --phase 118.4
    "$PYTHON" tidegrid.py add S2 --file station.json --amplitude 0.63 --phase 145.2
    "$PYTHON" tidegrid.py add N2 --file station.json --amplitude 0.72 --phase 96.7
    "$PYTHON" tidegrid.py add [redacted] --file station.json --amplitude 0.18 --phase 143.9
    "$PYTHON" tidegrid.py add K1 --file station.json --amplitude 0.11 --phase 275.3
    "$PYTHON" tidegrid.py add [redacted] --file station.json --amplitude 0.09 --phase 258.6
    "$PYTHON" tidegrid.py add P1 --file station.json --amplitude 0.04 --phase 273.1
    "$PYTHON" tidegrid.py add Q1 --file station.json --amplitude 0.02 --phase 250.4
    "$PYTHON" tidegrid.py add M4 --file station.json --amplitude 0.09 --phase 312.8
    "$PYTHON" tidegrid.py add M6 --file station.json --amplitude 0.03 --phase 45.6
fi
"$PYTHON" tidegrid.py validate --file station.json

echo
echo 'Setup complete. Try:'
echo "  $PYTHON tidegrid.py --help"
echo "  $PYTHON tidegrid.py predict --file station.json --date 2024-06-01"
echo "  bash demo.sh"
