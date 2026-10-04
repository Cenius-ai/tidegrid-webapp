# Installing and running Tidegrid

Tidegrid is a single-file, standard-library-only command-line tool. It has no server, no
database, no service and no required environment variable, so "installing" it means
putting one file somewhere and running it.

## Prerequisites

| Requirement | Notes |
| --- | --- |
| Python 3.9 or newer | `python3 --version`. CPython 3.11 is what this project is developed and tested against. |
| pip | Only needed for the optional console script and for running the test suite. |
| Nothing else | No compiler, no `libsqlite3`, no system package, no database server, no network access at runtime. |

## Install

### Step 1 — project dependencies, the console script and the demo station (one command)

```bash
bash install.sh
```

`install.sh` runs from the project root and:

1. checks the interpreter is 3.9+,
2. installs the declared dependencies with pip (`requirements.txt`; the tool itself needs
   none, the list pins the test runner) and upgrades pip/setuptools/wheel first,
3. installs the optional `tidegrid` console script (`pip install --no-build-isolation -e .`),
4. self-checks by importing the module and printing the catalog size,
5. **seeds** a demo `station.json` in the project root by running the tool's own
   `init`/`add` commands — skipped if the file already exists, so the step is idempotent,
6. validates the seeded station and prints the three commands to try next,
7. **exits**. It never starts a server, because Tidegrid is not a server.

If pip cannot reach a package index, the script says so and continues: the tool imports
nothing outside the standard library, and steps 4–6 are what actually need to succeed.

### Step 2 — verify the install

```bash
python3 -c 'import tidegrid; print(tidegrid.VERSION)'
python3 tidegrid.py --version
python3 tidegrid.py validate --file station.json
```

The last command must print one `ok` line and exit `0`.

### Step 3 — run the test suite

```bash
python3 -m pytest
```

58 tests covering the exit-code contract, the station file round trip, the harmonic
synthesis, the extremum scan and the CSV/text exporters. No network, no fixtures to
download, no interactive prompts.

## Seed data

The only seed step is step 1's `station.json`, seeded through the tool's own commands:

| Command | Effect |
| --- | --- |
| `python3 tidegrid.py init --file station.json --name 'Port Haven' --datum MLLW --z0 2.35 --units m --tz-offset -300` | creates the station |
| `python3 tidegrid.py add <SYMBOL> --file station.json --amplitude A --phase P` (×10) | adds M2, S2, N2, [redacted], K1, [redacted], P1, Q1, M4, M6 |
| `python3 tidegrid.py validate --file station.json` | proves the seeded file is clean |

To start from scratch instead, delete `station.json` and re-run `bash install.sh`, or
scaffold your own station with the commands in [USAGE.md](USAGE.md).

## Run

Tidegrid runs one command at a time and exits; there is no long-running process to start
and no port to bind.

```bash
python3 tidegrid.py --help
python3 tidegrid.py predict --file station.json --date 2024-06-01
```

With the console script installed (step 1), the same commands are:

```bash
tidegrid --help
tidegrid predict --file station.json --date 2024-06-01
```

## Demo

```bash
bash demo.sh
```

The script scaffolds a station in a scratch directory, edits its constants, predicts a
height and three days of high/low waters, exports CSV and text tables, checks the
documented exit codes and confirms that an exported row and `predict --at` agree. Its
captured output is committed as `demo/transcript.txt`; regenerate it with:

```bash
NO_COLOR=1 bash demo.sh > demo/transcript.txt
```

## Configuration

Tidegrid needs no configuration. It reads one environment variable, the conventional
`NO_COLOR`, which disables colour when set to any value; `--no-color` does the same per
invocation. Colour is never emitted when stdout is not a terminal.

## Uninstalling

Remove `tidegrid.py`, `station.json` and `examples/` — nothing is installed outside the
project directory except the optional console script, which
`python3 -m pip uninstall tidegrid` removes.
