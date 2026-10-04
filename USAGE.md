# Using Tidegrid

Every command takes `--file PATH` (default `station.json`) except `init`, which creates
that file, and `constituents`, which reads only the built-in catalog. All commands run and
exit; none of them wait for input.

## 1. Scaffold a station

```console
$ python3 tidegrid.py init --file station.json --name 'Port Haven' --datum MLLW --z0 2.35 --units m --tz-offset -300
created station.json
name             Port Haven
datum            MLLW
z0_m             2.35
units            m
timezone_offset  -05:00 (-300 minutes)

constituents      none yet - add one with: tidegrid add M2 --file station.json --amplitude 0.62 --phase 120
```

`--z0` is the chart datum offset Z0: it is added to every predicted height, so a station
whose mean level sits 2.35 m above chart datum says `--z0 2.35`. `init` refuses to touch an
existing file unless you pass `--force`, and `--force` rewrites a fresh scaffold, so keep a
copy if you are about to replace hand-tuned constants.

## 2. Add the harmonic constants

```console
$ python3 tidegrid.py constituents
┌──────┬────────────────────┬────────────────────────────────────┐
│ name │ speed_deg_per_hour │ description                        │
├──────┼────────────────────┼────────────────────────────────────┤
│ M2   │          28.984104 │ Principal lunar semidiurnal        │
│ S2   │          30.000000 │ Principal solar semidiurnal        │
│ N2   │          28.439730 │ Larger lunar elliptic semidiurnal  │
│ [redacted]   │          30.082137 │ Lunisolar semidiurnal              │
│ K1   │          15.041069 │ Lunisolar diurnal                  │
...
$ python3 tidegrid.py add M2 --file station.json --amplitude 3.72 --phase 118.4
added M2 to station.json (amplitude 3.72 m, phase 118.4 deg)
$ python3 tidegrid.py add S2 --file station.json --amplitude 0.63 --phase 145.2
added S2 to station.json (amplitude 0.63 m, phase 145.2 deg)
```

Symbols are case-sensitive and must exist in the catalog — `add NOTREAL` exits 2 with
`unknown constituent`. Amplitude is in metres and must be ≥ 0; phase is in degrees between
0 and 360. A duplicate symbol is refused and the stored values are left untouched.

`list` shows what is stored, with the catalog speed beside each term:

```console
$ python3 tidegrid.py list --file station.json
┌──────┬─────────────┬───────────┬────────────────────┐
│ name │ amplitude_m │ phase_deg │ speed_deg_per_hour │
├──────┼─────────────┼───────────┼────────────────────┤
│ M2   │        3.72 │     118.4 │          28.984104 │
│ S2   │        0.63 │     145.2 │          30.000000 │
│ N2   │        0.72 │      96.7 │          28.439730 │
│ K1   │        0.11 │     275.3 │          15.041069 │
│ [redacted]   │        0.09 │     258.6 │          13.943035 │
└──────┴─────────────┴───────────┴────────────────────┘
```

`remove NAME` deletes a term and says so, naming the symbol, if it is not there.

## 3. Edit station-level fields in place

```console
$ python3 tidegrid.py set --file station.json --z0 3.35
updated station.json
  z0_m: 2.35 -> 3.35
$ python3 tidegrid.py set --file station.json --units ft --name 'Port Haven N'
updated station.json
  name: Port Haven -> Port Haven N
  units: m -> ft
```

`--z0`, `--name`, `--datum`, `--units` and `--tz-offset` all write the file back atomically,
and every later `show`, `predict` and `export` uses the new values. Give at least one flag:
`tidegrid set` with none exits 1 with usage.

## 4. Inspect and validate

```console
$ python3 tidegrid.py show --file station.json
station station.json
name             Port Haven
datum            MLLW
z0_m             2.35
units            m
timezone_offset  -05:00 (-300 minutes)
constituents     5
$ python3 tidegrid.py validate --file station.json
station.json: ok - 5 constituents, z0_m 2.35, units m, datum MLLW
```

When something is wrong, `validate` prints one line per problem and exits 2:

```console
$ python3 tidegrid.py validate --file station.json
tidegrid: constituent XX: unknown name - run 'tidegrid constituents' for the catalog
tidegrid: constituent M2: amplitude_m must be a number
$ echo $?
2
```

## 5. Predict

A height at one instant:

```console
$ python3 tidegrid.py predict --file station.json --at '2024-06-01 00:00'
2024-06-01 00:00   5.555 m
```

The high and low waters of a day:

```console
$ python3 tidegrid.py predict --file station.json --date 2024-06-01
┌──────────────────┬──────┬────────┬───────┐
│ time             │ type │ height │ units │
├──────────────────┼──────┼────────┼───────┤
│ 2024-06-01 05:02 │ low  │ -1.295 │ m     │
│ 2024-06-01 11:08 │ high │  5.998 │ m     │
│ 2024-06-01 17:17 │ low  │ -1.590 │ m     │
│ 2024-06-01 23:31 │ high │  6.391 │ m     │
└──────────────────┴──────┴────────┴───────┘
```

A range of days works the same way and always reads in chronological order with highs and
lows alternating:

```console
$ python3 tidegrid.py predict --file station.json --from 2024-06-01 --to 2024-06-03
```

Heights are printed in the station's units and always include Z0. In the example above,
`set --z0 3.35` raises every one of the four rows by exactly 1.00 m — the fastest way to
see that the stored datum offset really drives the arithmetic.

## 6. Export a tide table

CSV to a file (the path and the row count go to stdout):

```console
$ python3 tidegrid.py export --file station.json --from 2024-06-01 --to 2024-06-03 --format csv --out tides.csv
wrote tides.csv (11 rows)
$ head -3 tides.csv
time,type,height,units
2024-06-01 05:02,low,-1.295,m
2024-06-01 11:08,high,5.998,m
```

Omit `--out` to stream exactly the same table to stdout for piping:

```bash
python3 tidegrid.py export --file station.json --from 2024-06-01 --to 2024-06-03 --format csv | grep ',high,'
python3 tidegrid.py export --file station.json --from 2024-06-01 --to 2024-06-03 --format txt
python3 tidegrid.py export --file station.json --from 2024-06-01 --to 2024-06-03 --format csv > june.csv
```

`--format csv` writes the header `time,type,height,units` and one row per high or low
water; `--format txt` writes the same four columns as a fixed-width table. Because the
exporter and `predict` share the station file and the arithmetic, an exported row and
`predict --at '<that time>'` always agree to the printed precision.

A range with no extrema at all (a station whose constants are all zero) still produces a
valid header-only CSV and exits 0.

## 7. Use it in a script

```bash
#!/usr/bin/env bash
set -euo pipefail
station=station.json
python3 tidegrid.py validate --file "$station" || exit 2
today=$(date +%F)
python3 tidegrid.py export --file "$station" --from "$today" --to "$today" --format csv --out "tides-$today.csv"
```

Exit codes are the contract: `0` success, `1` usage error with usage on stderr, `2` data or
validation error with one plain line per problem on stderr. Nothing is written to stdout on
failure, so `|` pipelines and `$(...)` capture stay clean:

```console
$ python3 tidegrid.py predict --file station.json --date 2024-13-45
tidegrid: '2024-13-45' is not a real calendar date - use YYYY-MM-DD, for example 2024-06-01
$ echo $?
2
```

## A realistic station to start from

`examples/port-haven.json` is a ten-constituent station on MLLW with a 2.35 m offset and a
UTC−05:00 clock. Point any command at it directly:

```bash
python3 tidegrid.py validate --file examples/port-haven.json
python3 tidegrid.py predict --file examples/port-haven.json --date 2024-06-01
python3 tidegrid.py export --file examples/port-haven.json --from 2024-06-01 --to 2024-06-30 --format csv --out june.csv
```
