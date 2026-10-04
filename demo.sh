#!/usr/bin/env bash
#
# Tidegrid end-to-end demo.
#
# Runs the whole product unattended: scaffold a station, edit its harmonic
# constants, inspect it, predict a height and a day of high/low waters, export a
# tide table and check that the documented exit codes really are 0/1/2.
#
# Everything happens inside a scratch directory, so the working tree is untouched.
# Run it with:  bash demo.sh          (add NO_COLOR=1 for a plain-text transcript)
#
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"
PYTHON="${PYTHON:-python3}"
TOOL="$ROOT/tidegrid.py"

WORK="$(mktemp -d)"
if [ "${KEEP_WORK:-0}" = "1" ]; then
    echo "scratch directory kept at $WORK" >&2
else
    trap 'rm -rf "$WORK"' EXIT
fi
cd "$WORK"

step() {
    printf '\n$ tidegrid %s\n' "$*"
    "$PYTHON" "$TOOL" "$@"
}

expect() {
    # expect <exit-code> <args...> - shows the one-line stderr contract
    local expected="$1"
    shift
    printf '\n$ tidegrid %s   # expect exit %s\n' "$*" "$expected"
    set +e
    "$PYTHON" "$TOOL" "$@" 2>&1 | sed 's/^/stderr: /'
    local code="${PIPESTATUS[0]}"
    set -e
    printf 'exit: %s\n' "$code"
    if [ "$code" -ne "$expected" ]; then
        echo "demo FAILED: expected exit $expected, got $code from: tidegrid $*" >&2
        exit 1
    fi
}

printf '=== Tidegrid demo ===\n'

step --version
step --help

step init --file station.json --name 'Port Haven' --datum MLLW --z0 2.35 --units m --tz-offset -300
step add M2 --file station.json --amplitude 3.72 --phase 118.4
step add S2 --file station.json --amplitude 0.63 --phase 145.2
step add N2 --file station.json --amplitude 0.72 --phase 96.7
step add K1 --file station.json --amplitude 0.11 --phase 275.3
step add [redacted] --file station.json --amplitude 0.09 --phase 258.6

step list --file station.json
step validate --file station.json
step show --file station.json

printf '\n--- editing the datum offset Z0 (it drives every height) ---\n'
step set --file station.json --z0 3.35
step predict --file station.json --at '2024-06-01 12:00'
step set --file station.json --units ft
step predict --file station.json --at '2024-06-01 12:00'
step set --file station.json --units m

printf '\n--- predictions ---\n'
step predict --file station.json --at '2024-06-01 00:00'
step predict --file station.json --date 2024-06-01
step predict --file station.json --from 2024-06-01 --to 2024-06-03

printf '\n--- tide tables ---\n'
step export --file station.json --from 2024-06-01 --to 2024-06-03 --format csv --out tides.csv
printf '\n$ cat tides.csv\n'
cat tides.csv
step export --file station.json --from 2024-06-01 --to 2024-06-03 --format txt

printf '\n--- the built-in constituent catalog (reads no station file) ---\n'
step constituents

printf '\n=== exit-code contract ===\n'
expect 1 frobnicate
expect 1 set --file station.json --units fathoms
expect 2 show --file missing.json
expect 2 predict --file station.json --date 2024-13-45
expect 2 add NOTREAL --file station.json --amplitude 0.5 --phase 10
expect 2 add M2 --file station.json --amplitude 0.10 --phase 5
expect 2 remove M6 --file station.json

printf '\n=== checks ===\n'
rows="$(( $(wc -l < tides.csv) - 1 ))"
printf 'tides.csv holds %s data rows\n' "$rows"
if ! grep -q ',high,' tides.csv || ! grep -q ',low,' tides.csv; then
    echo 'demo FAILED: tides.csv is missing a high or a low water row' >&2
    exit 1
fi

first_row="$(sed -n '2p' tides.csv)"
first_time="${first_row%%,*}"
exported_height="$(printf '%s' "$first_row" | cut -d, -f3)"
printf '\n$ tidegrid predict --file station.json --at "%s"   # the first exported row\n' "$first_time"
predicted="$(PYTHONIOENCODING=utf-8 "$PYTHON" "$TOOL" predict --file station.json --at "$first_time" | awk '{print $(NF-1)}')"
printf 'exported height %s == predicted height %s\n' "$exported_height" "$predicted"
if [ "$exported_height" != "$predicted" ]; then
    echo "demo FAILED: export and predict disagree on $first_time" >&2
    exit 1
fi

printf 'demo finished cleanly.\n'
