#!/usr/bin/env python3
"""Tidegrid - tidal harmonic constant editor and tide-table generator.

A single-file, standard-library-only command-line tool.  A station file stores the
harmonic constants of one tide station plus the chart datum offset ``z0_m``.
Heights are synthesised with

    h(t) = Z0 + sum_i A_i * cos(omega_i * t - phi_i)

where ``t`` is hours from 2000-01-01T00:00:00 UTC, ``omega_i`` the constituent
speed in degrees per hour, ``A_i`` the amplitude in metres and ``phi_i`` the
phase lag in degrees.  Times on the command line are station-local; the station's
``timezone_offset_minutes`` converts them to that epoch.

Exit codes: 0 success, 1 usage error, 2 data or validation error.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import NoReturn

PROGRAM = "tidegrid"
VERSION = "1.0.0"
DEFAULT_STATION_FILE = "station.json"

DATE_FORMAT = "%Y-%m-%d"
INSTANT_FORMAT = "%Y-%m-%d %H:%M"
INSTANT_INPUT_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M")
SUPPORTED_UNITS = ("m", "ft")
METRES_TO_FEET = 3.280839895013123
SCAN_STEP = timedelta(minutes=1)
MAX_TIMEZONE_OFFSET_MINUTES = 960

# Harmonic phases are referenced to this epoch (UTC).  Nothing here changes it:
# the same station file and instant always give the same height.
EPOCH_UTC = datetime(2000, 1, 1, 0, 0, 0)

# --- design tokens (calm-precise / terminal dark) ------------------------------
# One accent, OKLCH hue 264: #5578c1.  Kept in hex for non-CSS consumers and
# converted to 24-bit ANSI for the terminal.  Colour is decoration only - every
# signal in this tool is also carried by a word (high/low, ok/problems).
ACCENT_HEX = "#5578c1"
ACCENT_RGB = (85, 120, 193)
MUTED_RGB = (124, 132, 148)
_COLOR_ENABLED = False


class DataError(Exception):
    """A data, file or validation problem: one line on stderr, exit code 2."""

    def __init__(self, *messages: str) -> None:
        self.messages: tuple[str, ...] = tuple(messages) or ("unexpected station data error",)
        super().__init__(self.messages[0])


def set_color(enabled: bool) -> None:
    global _COLOR_ENABLED
    _COLOR_ENABLED = enabled


def _paint(text: str, code: str) -> str:
    return f"{code}{text}\x1b[0m" if _COLOR_ENABLED else text


def accent(text: str) -> str:
    return _paint(text, f"\x1b[38;2;{ACCENT_RGB[0]};{ACCENT_RGB[1]};{ACCENT_RGB[2]}m")


def muted(text: str) -> str:
    return _paint(text, f"\x1b[38;2;{MUTED_RGB[0]};{MUTED_RGB[1]};{MUTED_RGB[2]}m")


# --- catalog -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CatalogConstituent:
    """A standard tidal constituent and the angular speed that drives it."""

    name: str
    speed_deg_per_hour: float
    description: str


CATALOG: tuple[CatalogConstituent, ...] = (
    CatalogConstituent("M2", 28.984104, "Principal lunar semidiurnal"),
    CatalogConstituent("S2", 30.000000, "Principal solar semidiurnal"),
    CatalogConstituent("N2", 28.439730, "Larger lunar elliptic semidiurnal"),
    CatalogConstituent("[redacted]", 30.082137, "Lunisolar semidiurnal"),
    CatalogConstituent("K1", 15.041069, "Lunisolar diurnal"),
    CatalogConstituent("[redacted]", 13.943035, "Principal lunar diurnal"),
    CatalogConstituent("P1", 14.958931, "Principal solar diurnal"),
    CatalogConstituent("Q1", 13.398661, "Larger lunar elliptic diurnal"),
    CatalogConstituent("M4", 57.968208, "First shallow-water overtide of M2"),
    CatalogConstituent("M6", 86.952313, "Third shallow-water overtide of M2"),
)
CATALOG_BY_NAME: dict[str, CatalogConstituent] = {item.name: item for item in CATALOG}


# --- model ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConstituentTerm:
    """One harmonic term of a station: amplitude in metres, phase lag in degrees."""

    name: str
    amplitude_m: float | None
    phase_deg: float | None
    speed_deg_per_hour: float | None


@dataclass(frozen=True, slots=True)
class Station:
    """The contents of one station JSON file."""

    name: str
    datum: str
    z0_m: float | None
    units: str
    timezone_offset_minutes: int | None
    constituents: tuple[ConstituentTerm, ...]
    path: Path


@dataclass(frozen=True, slots=True)
class Extremum:
    """A high or low water found by the scan."""

    when: datetime
    kind: str
    height_m: float


# --- station file reading ------------------------------------------------------


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _read_term(item: dict[str, object]) -> ConstituentTerm:
    name = item.get("name")
    symbol = name.strip() if isinstance(name, str) else ""
    entry = CATALOG_BY_NAME.get(symbol)
    return ConstituentTerm(
        name=symbol,
        amplitude_m=_as_float(item.get("amplitude_m")),
        phase_deg=_as_float(item.get("phase_deg")),
        speed_deg_per_hour=entry.speed_deg_per_hour if entry else None,
    )


def parse_station(raw: object, path: Path) -> tuple[Station, tuple[str, ...]]:
    """Build a best-effort Station from decoded JSON plus a list of schema problems."""
    if not isinstance(raw, dict):
        blank = Station("", "", None, "", None, (), path)
        return blank, ("station file must contain a JSON object",)

    problems: list[str] = []
    raw_terms = raw.get("constituents", [])
    terms: list[ConstituentTerm] = []
    if not isinstance(raw_terms, list):
        problems.append("constituents: must be a list of constituent terms")
        raw_terms = []
    for index, item in enumerate(raw_terms):
        if not isinstance(item, dict):
            problems.append(
                f"constituents[{index}]: must be an object with name, amplitude_m and phase_deg"
            )
            continue
        terms.append(_read_term(item))

    station = Station(
        name=raw.get("name").strip() if isinstance(raw.get("name"), str) else "",
        datum=raw.get("datum").strip() if isinstance(raw.get("datum"), str) else "",
        z0_m=_as_float(raw.get("z0_m")),
        units=raw.get("units") if isinstance(raw.get("units"), str) else "",
        timezone_offset_minutes=_as_int(raw.get("timezone_offset_minutes")),
        constituents=tuple(terms),
        path=path,
    )
    problems.extend(validate_station(station))
    return station, tuple(problems)


def validate_station(station: Station) -> tuple[str, ...]:
    """Return one line per schema problem; an empty tuple means the station is usable."""
    problems: list[str] = []
    if not station.name:
        problems.append("name: a non-empty station name is required")
    if not station.datum:
        problems.append("datum: a non-empty chart datum label is required")
    if station.z0_m is None:
        problems.append(
            f"z0_m: missing - the datum offset is required; set it with: {PROGRAM} set --z0 VALUE"
        )
    if station.units not in SUPPORTED_UNITS:
        problems.append(f"units: must be 'm' or 'ft' (got {station.units or 'nothing'})")
    offset = station.timezone_offset_minutes
    if offset is None:
        problems.append(
            "timezone_offset_minutes: missing - use whole minutes, for example 0 or -300"
        )
    elif not -MAX_TIMEZONE_OFFSET_MINUTES <= offset <= MAX_TIMEZONE_OFFSET_MINUTES:
        problems.append(
            f"timezone_offset_minutes: {offset} is outside "
            f"{-MAX_TIMEZONE_OFFSET_MINUTES}..{MAX_TIMEZONE_OFFSET_MINUTES}"
        )

    seen: set[str] = set()
    for index, term in enumerate(station.constituents):
        label = term.name or f"constituents[{index}]"
        if not term.name:
            problems.append(f"constituents[{index}]: a non-empty constituent name is required")
            continue
        if term.speed_deg_per_hour is None:
            problems.append(
                f"constituent {label}: unknown name - run '{PROGRAM} constituents' for the catalog"
            )
        if term.amplitude_m is None:
            problems.append(f"constituent {label}: amplitude_m must be a number")
        elif term.amplitude_m < 0:
            problems.append(f"constituent {label}: amplitude_m must be a non-negative number")
        if term.phase_deg is None:
            problems.append(f"constituent {label}: phase_deg must be a number")
        elif not 0.0 <= term.phase_deg <= 360.0:
            problems.append(f"constituent {label}: phase_deg must be between 0 and 360 degrees")
        if label in seen:
            problems.append(f"constituent {label}: listed more than once")
        seen.add(label)
    return tuple(problems)


def load_station(path: Path) -> tuple[Station, tuple[str, ...]]:
    """Read and parse a station file. Raises DataError if it is missing or unreadable."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise DataError(
            f"station file '{path}' not found - create it with: {PROGRAM} init --file {path}"
        ) from None
    except OSError as error:
        raise DataError(f"cannot read '{path}': {error.strerror or error}") from None
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        raise DataError(
            f"{path} is not valid JSON: {error.msg} at line {error.lineno} column {error.colno}"
        ) from None
    return parse_station(raw, path)


def require_usable(station: Station, problems: Sequence[str]) -> Station:
    """Reject a station the synthesis cannot trust, naming the first problem only."""
    if problems:
        raise DataError(f"{problems[0]} (run '{PROGRAM} validate --file {station.path}' for all)")
    return station


def require_constituents(station: Station) -> Station:
    if not station.constituents:
        raise DataError(
            f"{station.path} has no constituents - add one with: "
            f"{PROGRAM} add M2 --file {station.path} --amplitude 0.62 --phase 120"
        )
    return station


# --- station file writing ------------------------------------------------------


def station_payload(station: Station) -> dict[str, object]:
    return {
        "name": station.name,
        "datum": station.datum,
        "z0_m": station.z0_m,
        "units": station.units,
        "timezone_offset_minutes": station.timezone_offset_minutes,
        "constituents": [
            {
                "name": term.name,
                "amplitude_m": term.amplitude_m,
                "phase_deg": term.phase_deg,
            }
            for term in station.constituents
        ],
    }


def write_text_file(path: Path, text: str, *, create_parents: bool = False) -> None:
    """Write atomically, so a failed edit never leaves a half-written station file."""
    temporary: Path | None = None
    try:
        if create_parents and str(path.parent) not in ("", "."):
            path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent if str(path.parent) else Path("."),
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            temporary = Path(handle.name)
        os.replace(temporary, path)
        # tempfile hands back a 0600 file; a station file should follow the umask
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(path, 0o666 & ~umask)
    except OSError as error:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise DataError(f"cannot write '{path}': {error.strerror or error}") from None


def save_station(station: Station) -> None:
    text = json.dumps(station_payload(station), indent=2) + "\n"
    write_text_file(station.path, text, create_parents=True)


# --- parsing helpers -----------------------------------------------------------


def parse_date(text: str) -> datetime:
    try:
        return datetime.strptime(text.strip(), DATE_FORMAT)
    except ValueError:
        raise DataError(
            f"'{text}' is not a real calendar date - use YYYY-MM-DD, for example 2024-06-01"
        ) from None


def parse_instant(text: str) -> datetime:
    cleaned = text.strip()
    for pattern in INSTANT_INPUT_FORMATS:
        try:
            return datetime.strptime(cleaned, pattern)
        except ValueError:
            continue
    raise DataError(
        f"'{text}' is not a valid instant - use 'YYYY-MM-DD HH:MM', for example '2024-06-01 12:00'"
    )


def parse_day_range(start_text: str, end_text: str) -> tuple[datetime, datetime]:
    """Inclusive local-day range: start 00:00 to end 23:59."""
    start = parse_date(start_text)
    end = parse_date(end_text)
    if end < start:
        raise DataError(f"range end {end_text} is before the start {start_text}")
    return start, end + timedelta(days=1) - SCAN_STEP


def parse_number(text: str, flag: str) -> float:
    try:
        value = float(text.strip())
    except (AttributeError, ValueError):
        raise DataError(f"{flag}: '{text}' must be a number") from None
    if not math.isfinite(value):
        raise DataError(f"{flag}: '{text}' must be a finite number")
    return value


def parse_amplitude(text: str) -> float:
    try:
        value = float(text.strip())
    except (AttributeError, ValueError):
        raise DataError(f"--amplitude: '{text}' must be a non-negative number") from None
    if not math.isfinite(value) or value < 0:
        raise DataError(f"--amplitude: '{text}' must be a non-negative number")
    return value


def parse_phase(text: str) -> float:
    try:
        value = float(text.strip())
    except (AttributeError, ValueError):
        raise DataError(f"--phase: '{text}' must be a number between 0 and 360 degrees") from None
    if not math.isfinite(value) or not 0.0 <= value <= 360.0:
        raise DataError(f"--phase: '{text}' must be a number between 0 and 360 degrees")
    return value


def parse_timezone_offset(text: str) -> int:
    try:
        value = int(text.strip())
    except (AttributeError, ValueError):
        raise DataError(f"--tz-offset: '{text}' must be a whole number of minutes") from None
    if not -MAX_TIMEZONE_OFFSET_MINUTES <= value <= MAX_TIMEZONE_OFFSET_MINUTES:
        raise DataError(
            f"--tz-offset: {value} is outside "
            f"{-MAX_TIMEZONE_OFFSET_MINUTES}..{MAX_TIMEZONE_OFFSET_MINUTES} minutes"
        )
    return value


# --- harmonic synthesis --------------------------------------------------------


def hours_since_epoch(when_local: datetime, timezone_offset_minutes: int) -> float:
    local = when_local.replace(tzinfo=None)
    return (local - EPOCH_UTC).total_seconds() / 3600.0 - timezone_offset_minutes / 60.0


def predict_height_m(station: Station, when_local: datetime) -> float:
    """h(t) = Z0 + sum A_i * cos(omega_i * t - phi_i)."""
    hours = hours_since_epoch(when_local, station.timezone_offset_minutes or 0)
    height = station.z0_m or 0.0
    for term in station.constituents:
        if term.speed_deg_per_hour is None or term.amplitude_m is None:
            continue
        angle = math.radians(term.speed_deg_per_hour * hours - (term.phase_deg or 0.0))
        height += term.amplitude_m * math.cos(angle)
    return height


def height_rate_m_per_hour(station: Station, when_local: datetime) -> float:
    """d h / d t, in metres per hour - the sign tells high from low water."""
    hours = hours_since_epoch(when_local, station.timezone_offset_minutes or 0)
    rate = 0.0
    for term in station.constituents:
        if term.speed_deg_per_hour is None or term.amplitude_m is None:
            continue
        angle = math.radians(term.speed_deg_per_hour * hours - (term.phase_deg or 0.0))
        rate -= term.amplitude_m * term.speed_deg_per_hour * math.sin(angle)
    return rate * math.pi / 180.0


def _round_to_minute(when: datetime) -> datetime:
    seconds = when.hour * 3600 + when.minute * 60 + when.second + when.microsecond / 1e6
    midnight = when.replace(hour=0, minute=0, second=0, microsecond=0)
    total = round(seconds / 60.0) * 60
    day_shift, seconds_in_day = divmod(total, 86400)
    return midnight + timedelta(days=day_shift, seconds=seconds_in_day)


def _refine_vertex(station: Station, left: datetime, right: datetime) -> datetime:
    """Parabolic fit through three samples; the vertex is within [left, right]."""
    middle = left + (right - left) / 2
    h_left = predict_height_m(station, left)
    h_middle = predict_height_m(station, middle)
    h_right = predict_height_m(station, right)
    curvature = h_left - 2 * h_middle + h_right
    offset = 0.0 if curvature == 0.0 else 0.25 * (h_left - h_right) / curvature
    fraction = min(max(0.5 + offset, 0.0), 1.0)
    return left + (right - left) * fraction


def find_extrema(station: Station, start: datetime, end: datetime) -> list[Extremum]:
    """Scan minute by minute for sign changes in the height slope, then refine."""
    steps = int((end - start).total_seconds() // 60)
    extrema: list[Extremum] = []
    previous_time = start
    previous_rate = height_rate_m_per_hour(station, previous_time)
    for step in range(1, steps + 1):
        current_time = start + timedelta(minutes=step)
        current_rate = height_rate_m_per_hour(station, current_time)
        if (
            previous_rate != 0.0
            and current_rate != 0.0
            and (previous_rate > 0.0) != (current_rate > 0.0)
        ):
            when = _round_to_minute(_refine_vertex(station, previous_time, current_time))
            when = min(max(when, start), end)
            if not extrema or when != extrema[-1].when:
                kind = "high" if previous_rate > 0.0 else "low"
                extrema.append(Extremum(when, kind, predict_height_m(station, when)))
        previous_time, previous_rate = current_time, current_rate
    return extrema


# --- output helpers ------------------------------------------------------------


def convert_height(height_m: float, units: str) -> float:
    return height_m * METRES_TO_FEET if units == "ft" else height_m


def format_height(height_m: float, units: str) -> str:
    return f"{convert_height(height_m, units):.3f} {units}"


def format_number(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:g}"


def format_offset(offset: int | None) -> str:
    if offset is None:
        return "-"
    sign = "+" if offset >= 0 else "-"
    hours, minutes = divmod(abs(offset), 60)
    return f"{sign}{hours:02d}:{minutes:02d} ({offset} minutes)"


def render_fixed_table(
    headers: Sequence[str], rows: Sequence[Sequence[str]], aligns: Sequence[str] | None = None
) -> str:
    """An aligned, box-drawn table - the tool's one shape for tabular output."""
    alignments = list(aligns) if aligns else ["left"] * len(headers)
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def border(left: str, joint: str, right: str) -> str:
        return left + joint.join("\u2500" * (width + 2) for width in widths) + right

    def line(cells: Sequence[str]) -> str:
        padded = [
            cell.rjust(widths[index]) if alignments[index] == "right" else cell.ljust(widths[index])
            for index, cell in enumerate(cells)
        ]
        return "\u2502" + "\u2502".join(f" {cell} " for cell in padded) + "\u2502"

    lines = [muted(border("\u250c", "\u252c", "\u2510")), accent(line(headers))]
    lines.append(muted(border("\u251c", "\u253c", "\u2524")))
    lines.extend(line(row) for row in rows)
    lines.append(muted(border("\u2514", "\u2534", "\u2518")))
    return "\n".join(lines) + "\n"


def render_label_lines(pairs: Sequence[tuple[str, str]]) -> str:
    width = max(len(label) for label, _ in pairs)
    return "".join(f"{label.ljust(width)}  {value}\n" for label, value in pairs)


TABLE_HEADERS = ("time", "type", "height", "units")
TABLE_ALIGNS = ("left", "left", "right", "left")


def extremum_rows(station: Station, extrema: Sequence[Extremum]) -> list[list[str]]:
    return [
        [
            row.when.strftime(INSTANT_FORMAT),
            row.kind,
            f"{convert_height(row.height_m, station.units):.3f}",
            station.units,
        ]
        for row in extrema
    ]


def render_csv(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(list(headers))
    writer.writerows([list(row) for row in rows])
    return buffer.getvalue()


# --- commands ------------------------------------------------------------------


def usage_error(args: argparse.Namespace, message: str) -> NoReturn:
    """Usage problems exit 1 with usage on stderr, keeping them distinct from data errors."""
    args.subparser.error(message)


def command_init(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if path.exists() and not args.force:
        raise DataError(f"station file '{path}' already exists - pass --force to overwrite it")
    station = Station(
        name=args.name.strip() if args.name else path.stem or "Unnamed station",
        datum=args.datum.strip(),
        z0_m=parse_number(args.z0, "--z0"),
        units=args.units,
        timezone_offset_minutes=parse_timezone_offset(args.tz_offset),
        constituents=(),
        path=path,
    )
    problems = validate_station(station)
    if problems:
        raise DataError(*problems)
    save_station(station)
    print(f"{accent('created')} {path}")
    print(render_label_lines(station_summary_lines(station, include_constituents=False)))
    print(f"constituents      none yet - add one with: {PROGRAM} add M2 --file {path} "
          "--amplitude 0.62 --phase 120")
    return 0


def station_summary_lines(
    station: Station, *, include_constituents: bool = True
) -> list[tuple[str, str]]:
    lines = [
        ("name", station.name or "(missing)"),
        ("datum", station.datum or "(missing)"),
        ("z0_m", format_number(station.z0_m) if station.z0_m is not None else "(missing)"),
        ("units", station.units or "(missing)"),
        ("timezone_offset", format_offset(station.timezone_offset_minutes)),
    ]
    if include_constituents:
        lines.append(("constituents", str(len(station.constituents))))
    return lines


def command_show(args: argparse.Namespace) -> int:
    station, _problems = load_station(Path(args.file))
    print(f"{accent('station')} {station.path}")
    print(render_label_lines(station_summary_lines(station)))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    station, problems = load_station(Path(args.file))
    if problems:
        raise DataError(*problems)
    print(
        f"{station.path}: ok - {len(station.constituents)} constituents, "
        f"z0_m {format_number(station.z0_m)}, units {station.units}, datum {station.datum}"
    )
    return 0


def command_set(args: argparse.Namespace) -> int:
    station, problems = load_station(Path(args.file))
    require_usable(station, problems)

    changes: list[tuple[str, str, str]] = []
    name, datum, units = station.name, station.datum, station.units
    z0_m = station.z0_m or 0.0
    offset = station.timezone_offset_minutes or 0
    if args.z0 is not None:
        new_z0 = parse_number(args.z0, "--z0")
        changes.append(("z0_m", format_number(z0_m), format_number(new_z0)))
        z0_m = new_z0
    if args.name is not None:
        if not args.name.strip():
            raise DataError("--name: a non-empty station name is required")
        changes.append(("name", name, args.name.strip()))
        name = args.name.strip()
    if args.datum is not None:
        if not args.datum.strip():
            raise DataError("--datum: a non-empty chart datum label is required")
        changes.append(("datum", datum, args.datum.strip()))
        datum = args.datum.strip()
    if args.units is not None:
        changes.append(("units", units, args.units))
        units = args.units
    if args.tz_offset is not None:
        new_offset = parse_timezone_offset(args.tz_offset)
        changes.append(("timezone_offset_minutes", str(offset), str(new_offset)))
        offset = new_offset

    if not changes:
        usage_error(
            args,
            "nothing to change - pass at least one of --z0, --name, --datum, --units, --tz-offset",
        )

    updated = Station(
        name=name,
        datum=datum,
        z0_m=z0_m,
        units=units,
        timezone_offset_minutes=offset,
        constituents=station.constituents,
        path=station.path,
    )
    remaining = validate_station(updated)
    if remaining:
        raise DataError(*remaining)
    save_station(updated)
    print(f"{accent('updated')} {station.path}")
    for field, before, after in changes:
        print(f"  {field}: {before} -> {after}")
    return 0


def command_constituents(args: argparse.Namespace) -> int:
    rows = [
        [item.name, f"{item.speed_deg_per_hour:.6f}", item.description] for item in CATALOG
    ]
    sys.stdout.write(
        render_fixed_table(
            ("name", "speed_deg_per_hour", "description"), rows, ("left", "right", "left")
        )
    )
    return 0


def command_list(args: argparse.Namespace) -> int:
    station, _problems = load_station(Path(args.file))
    if not station.constituents:
        print(
            "no constituents yet - add one with: "
            f"{PROGRAM} add M2 --file {station.path} --amplitude 0.62 --phase 120"
        )
        return 0
    rows = [
        [
            term.name,
            format_number(term.amplitude_m),
            format_number(term.phase_deg),
            f"{term.speed_deg_per_hour:.6f}" if term.speed_deg_per_hour is not None else "-",
        ]
        for term in station.constituents
    ]
    sys.stdout.write(
        render_fixed_table(
            ("name", "amplitude_m", "phase_deg", "speed_deg_per_hour"),
            rows,
            ("left", "right", "right", "right"),
        )
    )
    return 0


def command_add(args: argparse.Namespace) -> int:
    path = Path(args.file)
    station, problems = load_station(path)
    require_usable(station, problems)

    symbol = args.name.strip()
    if not symbol:
        raise DataError("NAME: a non-empty constituent symbol is required")
    entry = CATALOG_BY_NAME.get(symbol)
    if entry is None:
        raise DataError(
            f"{symbol}: unknown constituent - run '{PROGRAM} constituents' for the catalog"
        )
    amplitude = parse_amplitude(args.amplitude)
    phase = parse_phase(args.phase)
    if any(term.name == symbol for term in station.constituents):
        raise DataError(
            f"{symbol}: already present in {path} - remove it first with: "
            f"{PROGRAM} remove {symbol} --file {path}"
        )

    term = ConstituentTerm(symbol, amplitude, phase, entry.speed_deg_per_hour)
    updated = Station(
        name=station.name,
        datum=station.datum,
        z0_m=station.z0_m,
        units=station.units,
        timezone_offset_minutes=station.timezone_offset_minutes,
        constituents=station.constituents + (term,),
        path=path,
    )
    save_station(updated)
    print(
        f"{accent('added')} {symbol} to {path} "
        f"(amplitude {format_number(amplitude)} m, phase {format_number(phase)} deg)"
    )
    return 0


def command_remove(args: argparse.Namespace) -> int:
    path = Path(args.file)
    station, problems = load_station(path)
    require_usable(station, problems)

    symbol = args.name.strip()
    remaining = tuple(term for term in station.constituents if term.name != symbol)
    if len(remaining) == len(station.constituents):
        raise DataError(
            f"{symbol}: not present in {path} - run '{PROGRAM} list --file {path}' "
            "to see the stored constituents"
        )
    updated = Station(
        name=station.name,
        datum=station.datum,
        z0_m=station.z0_m,
        units=station.units,
        timezone_offset_minutes=station.timezone_offset_minutes,
        constituents=remaining,
        path=path,
    )
    save_station(updated)
    print(
        f"{accent('removed')} {symbol} from {path} "
        f"({len(remaining)} constituent{'s' if len(remaining) != 1 else ''} remain)"
    )
    return 0


def command_predict(args: argparse.Namespace) -> int:
    path = Path(args.file)
    station, problems = load_station(path)
    require_usable(station, problems)
    require_constituents(station)

    selectors = [bool(args.at), bool(args.date), bool(args.start or args.end)]
    if sum(selectors) > 1:
        usage_error(args, "use only one of --at, --date or --from/--to")
    if sum(selectors) == 0:
        usage_error(args, "specify --at INSTANT, --date DATE or --from DATE --to DATE")
    if bool(args.start) != bool(args.end):
        usage_error(args, "--from and --to must be given together")

    if args.at:
        when = parse_instant(args.at)
        height = predict_height_m(station, when)
        print(f"{when.strftime(INSTANT_FORMAT)}   {format_height(height, station.units)}")
        return 0

    if args.date:
        start = parse_date(args.date)
        end = start + timedelta(days=1) - SCAN_STEP
    else:
        start, end = parse_day_range(args.start, args.end)

    extrema = find_extrema(station, start, end)
    sys.stdout.write(
        render_fixed_table(TABLE_HEADERS, extremum_rows(station, extrema), TABLE_ALIGNS)
    )
    return 0


def command_export(args: argparse.Namespace) -> int:
    path = Path(args.file)
    station, problems = load_station(path)
    require_usable(station, problems)
    require_constituents(station)

    start, end = parse_day_range(args.start, args.end)
    rows = extremum_rows(station, find_extrema(station, start, end))

    if args.format == "csv":
        text = render_csv(TABLE_HEADERS, rows)
    else:
        text = render_fixed_table(TABLE_HEADERS, rows, TABLE_ALIGNS)

    if args.out:
        out_path = Path(args.out)
        write_text_file(out_path, text)
        print(f"{accent('wrote')} {out_path} ({len(rows)} rows)")
    else:
        sys.stdout.write(text)
    return 0


# --- argument parser -----------------------------------------------------------


EXAMPLES = """examples:
  tidegrid init --file station.json --name 'Port Haven' --datum MLLW --z0 2.35
  tidegrid add M2 --file station.json --amplitude 3.72 --phase 118.4
  tidegrid show --file station.json
  tidegrid predict --file station.json --date 2024-06-01
  tidegrid export --file station.json --from 2024-06-01 --to 2024-06-03 --format csv --out tides.csv

exit codes: 0 success, 1 usage error, 2 data or validation error
"""


class TidegridParser(argparse.ArgumentParser):
    """Usage problems go to stderr and exit 1, so they never look like data errors."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def build_parser() -> TidegridParser:
    parser = TidegridParser(
        prog=PROGRAM,
        description="Edit tidal harmonic constants and predict high and low waters.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EXAMPLES,
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {VERSION}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND", title="commands")

    def add_command(name: str, help_text: str, description: str, **kwargs: object):
        sub = subparsers.add_parser(
            name,
            help=help_text,
            description=description,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            **kwargs,
        )
        sub.set_defaults(subparser=sub)
        return sub

    def add_station_file(sub: TidegridParser) -> None:
        sub.add_argument(
            "--file",
            default=DEFAULT_STATION_FILE,
            metavar="PATH",
            help=f"station file to read or write (default: {DEFAULT_STATION_FILE})",
        )
        sub.add_argument(
            "--no-color",
            action="store_true",
            help="disable colour (also honoured via the NO_COLOR environment variable)",
        )

    init = add_command(
        "init",
        "create a station file",
        "Create a station file with the datum offset and an empty constituent list.",
    )
    add_station_file(init)
    init.add_argument("--name", help="station name")
    init.add_argument("--datum", default="MLLW", help="chart datum label (default: MLLW)")
    init.add_argument("--z0", default="0", metavar="M", help="datum offset Z0 in metres")
    init.add_argument("--units", choices=SUPPORTED_UNITS, default="m", help="display units")
    init.add_argument(
        "--tz-offset", default="0", metavar="MINUTES", help="local time offset from UTC in minutes"
    )
    init.add_argument("--force", action="store_true", help="overwrite an existing station file")
    init.set_defaults(handler=command_init)

    show = add_command("show", "print the station summary", "Print the station summary.")
    add_station_file(show)
    show.set_defaults(handler=command_show)

    validate = add_command(
        "validate",
        "check the station file",
        "Check the station file and print one line per schema problem.",
    )
    add_station_file(validate)
    validate.set_defaults(handler=command_validate)

    set_cmd = add_command(
        "set",
        "edit station-level fields",
        "Edit station-level fields in place. Every flag is optional; give at least one.",
    )
    add_station_file(set_cmd)
    set_cmd.add_argument("--z0", metavar="M", help="new datum offset Z0 in metres")
    set_cmd.add_argument("--name", help="new station name")
    set_cmd.add_argument("--datum", help="new chart datum label")
    set_cmd.add_argument("--units", choices=SUPPORTED_UNITS, help="new display units")
    set_cmd.add_argument("--tz-offset", metavar="MINUTES", help="new local time offset from UTC")
    set_cmd.set_defaults(handler=command_set)

    constituents = add_command(
        "constituents",
        "list the built-in constituent catalog",
        "Print the built-in constituent catalog (name, speed in degrees per hour, description).",
    )
    constituents.set_defaults(handler=command_constituents)

    list_cmd = add_command(
        "list", "list the station's constituents", "List the station's harmonic constants."
    )
    add_station_file(list_cmd)
    list_cmd.set_defaults(handler=command_list)

    add = add_command(
        "add",
        "append a harmonic constant",
        "Append a harmonic constant, resolving NAME against the built-in catalog.",
    )
    add_station_file(add)
    add.add_argument("name", metavar="NAME", help="constituent symbol, for example M2")
    add.add_argument(
        "--amplitude", required=True, metavar="A", help="amplitude in metres (must be >= 0)"
    )
    add.add_argument("--phase", required=True, metavar="P", help="phase lag in degrees (0-360)")
    add.set_defaults(handler=command_add)

    remove = add_command(
        "remove", "delete a harmonic constant", "Delete a harmonic constant from the station file."
    )
    add_station_file(remove)
    remove.add_argument("name", metavar="NAME", help="constituent symbol to delete")
    remove.set_defaults(handler=command_remove)

    predict = add_command(
        "predict",
        "predict height or high/low waters",
        "Predict the water height at an instant, or the high and low waters of a date range.\n\n"
        "examples:\n"
        "  tidegrid predict --at '2024-06-01 12:00'\n"
        "  tidegrid predict --date 2024-06-01\n"
        "  tidegrid predict --from 2024-06-01 --to 2024-06-03",
    )
    add_station_file(predict)
    predict.add_argument("--at", metavar="'YYYY-MM-DD HH:MM'", help="height at one instant")
    predict.add_argument("--date", metavar="YYYY-MM-DD", help="high/low waters for one day")
    predict.add_argument("--from", dest="start", metavar="YYYY-MM-DD", help="range start day")
    predict.add_argument("--to", dest="end", metavar="YYYY-MM-DD", help="range end day")
    predict.set_defaults(handler=command_predict)

    export = add_command(
        "export",
        "write a tide table",
        "Write a tide table for a date range to --out PATH, or stream it to stdout.\n\n"
        "examples:\n"
        "  tidegrid export --from 2024-06-01 --to 2024-06-03 --format csv --out tides.csv\n"
        "  tidegrid export --from 2024-06-01 --to 2024-06-03 --format txt",
    )
    add_station_file(export)
    export.add_argument(
        "--from", dest="start", required=True, metavar="YYYY-MM-DD", help="range start day"
    )
    export.add_argument(
        "--to", dest="end", required=True, metavar="YYYY-MM-DD", help="range end day"
    )
    export.add_argument(
        "--format", choices=("csv", "txt"), default="csv", help="table format (default: csv)"
    )
    export.add_argument("--out", metavar="PATH", help="write to PATH instead of stdout")
    export.set_defaults(handler=command_export)

    return parser


# --- entry point ---------------------------------------------------------------


def _want_color(args: argparse.Namespace) -> bool:
    if getattr(args, "no_color", False):
        return False
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    return sys.stdout.isatty()


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not arguments:
        parser.print_help(sys.stdout)
        return 0

    args = parser.parse_args(arguments)
    set_color(_want_color(args))

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 1

    try:
        return handler(args)
    except DataError as error:
        for message in error.messages:
            print(f"{PROGRAM}: {message}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"{PROGRAM}: {error.strerror or error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(f"{PROGRAM}: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
