"""Tidegrid's own suite: the CLI contract, the harmonic core and the tide tables."""

from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from conftest import ROOT, run_tool

import tidegrid

# --- T1: skeleton, dispatch and the 0/1/2 exit-code contract --------------------


def test_bare_invocation_prints_help_and_exits_zero():
    result = run_tool()
    assert result.returncode == 0
    assert "usage: tidegrid" in result.stdout
    for command in ("init", "show", "validate", "set", "constituents", "list", "add", "remove",
                    "predict", "export"):
        assert command in result.stdout


def test_version_flag():
    result = run_tool("--version")
    assert result.returncode == 0
    assert result.stdout.strip() == f"tidegrid {tidegrid.VERSION}"


def test_unknown_command_is_a_usage_error_on_stderr():
    result = run_tool("frobnicate")
    assert result.returncode == 1
    assert result.stdout == ""
    assert "usage: tidegrid" in result.stderr


def test_missing_station_file_is_a_data_error():
    result = run_tool("show", "--file", "missing.json", cwd=ROOT / "tests")
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1
    assert "missing.json" in result.stderr and "init" in result.stderr


def test_no_args_prints_usage_and_exits_one():
    result = run_tool("--no-color")
    assert result.returncode == 1


# --- T2: init and z0_m ---------------------------------------------------------


def test_init_writes_the_scaffold_and_reports_z0(tmp_path):
    path = tmp_path / "station.json"
    result = run_tool(
        "init", "--file", str(path), "--name", "Port X", "--datum", "MLLW", "--z0", "2.5"
    )
    assert result.returncode == 0, result.stderr
    assert "z0_m" in result.stdout and "2.5" in result.stdout
    payload = json.loads(path.read_text())
    assert payload["z0_m"] == 2.5
    assert payload["name"] == "Port X"
    assert payload["datum"] == "MLLW"
    assert payload["units"] == "m"
    assert payload["timezone_offset_minutes"] == 0
    assert payload["constituents"] == []


def test_init_refuses_to_overwrite_without_force(tmp_path):
    path = tmp_path / "station.json"
    assert run_tool("init", "--file", str(path), "--name", "Port X").returncode == 0
    again = run_tool("init", "--file", str(path), "--name", "Other")
    assert again.returncode == 2
    assert again.stdout == ""
    assert "already exists" in again.stderr and "--force" in again.stderr
    assert json.loads(path.read_text())["name"] == "Port X"


def test_init_force_rewrites_a_fresh_scaffold(tmp_path):
    path = tmp_path / "station.json"
    run_tool("init", "--file", str(path), "--name", "Port X", "--z0", "1.0")
    run_tool("add", "M2", "--file", str(path), "--amplitude", "1.0", "--phase", "10")
    forced = run_tool("init", "--file", str(path), "--name", "Port Y", "--z0", "4.0", "--force")
    assert forced.returncode == 0, forced.stderr
    payload = json.loads(path.read_text())
    assert payload["name"] == "Port Y"
    assert payload["z0_m"] == 4.0
    assert payload["constituents"] == []


# --- T3: the load guard is scoped to station-reading subcommands ---------------


def test_init_is_exempt_from_the_missing_file_guard(tmp_path):
    path = tmp_path / "fresh.json"
    result = run_tool("init", "--file", str(path), "--name", "Port X")
    assert result.returncode == 0
    assert path.exists()


def test_constituents_never_reads_the_station_file(tmp_path):
    result = run_tool("constituents", cwd=tmp_path)
    assert result.returncode == 0
    assert "M2" in result.stdout


@pytest.mark.parametrize("command", ["show", "validate", "list", "predict", "export"])
def test_station_reading_commands_fail_without_a_file(command, tmp_path):
    args = {
        "predict": ("predict", "--at", "2024-06-01 00:00"),
        "export": ("export", "--from", "2024-06-01", "--to", "2024-06-01"),
    }.get(command, (command,))
    result = run_tool(*args, cwd=tmp_path)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "not found" in result.stderr


# --- T4: the constituent catalog ----------------------------------------------


def test_catalog_contains_m2_with_the_standard_speed():
    m2 = tidegrid.CATALOG_BY_NAME["M2"]
    assert m2.speed_deg_per_hour == pytest.approx(28.984104)
    assert m2.description


def test_constituents_command_prints_every_symbol():
    result = run_tool("constituents")
    assert result.returncode == 0
    for item in tidegrid.CATALOG:
        assert item.name in result.stdout
    assert "28.984104" in result.stdout
    assert "Principal lunar semidiurnal" in result.stdout


def test_add_rejects_an_unknown_constituent(station):
    result = run_tool("add", "NOTREAL", "--file", str(station),
                      "--amplitude", "0.5", "--phase", "10")
    assert result.returncode == 2
    assert result.stdout == ""
    assert "NOTREAL" in result.stderr and "unknown constituent" in result.stderr


# --- T5: show and validate -----------------------------------------------------


def test_show_echoes_the_station_summary(station):
    result = run_tool("show", "--file", str(station))
    assert result.returncode == 0
    for expected in ("Port Haven", "MLLW", "2.35", "m", "5"):
        assert expected in result.stdout
    assert "timezone_offset" in result.stdout


def test_validate_reports_every_problem_and_fails(station):
    payload = json.loads(station.read_text())
    payload["constituents"].append({"name": "XX", "amplitude_m": 0.4, "phase_deg": 12})
    payload["constituents"][0]["amplitude_m"] = "big"
    station.write_text(json.dumps(payload))

    result = run_tool("validate", "--file", str(station))
    assert result.returncode == 2
    assert result.stdout == ""
    lines = result.stderr.strip().splitlines()
    assert any("XX" in line and "unknown name" in line for line in lines)
    assert any("amplitude_m must be a number" in line for line in lines)


def test_validate_accepts_a_well_formed_station(station):
    result = run_tool("validate", "--file", str(station))
    assert result.returncode == 0
    assert "ok" in result.stdout
    assert result.stderr == ""


def test_validate_reports_a_missing_z0(station):
    payload = json.loads(station.read_text())
    del payload["z0_m"]
    station.write_text(json.dumps(payload))
    result = run_tool("validate", "--file", str(station))
    assert result.returncode == 2
    assert "z0_m" in result.stderr


def test_corrupt_json_is_a_single_line_data_error(station):
    station.write_text("{not json")
    result = run_tool("show", "--file", str(station))
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1
    assert "not valid JSON" in result.stderr


# --- T6: set -------------------------------------------------------------------


def test_set_z0_persists_and_show_reports_it(station):
    result = run_tool("set", "--file", str(station), "--z0", "3.1")
    assert result.returncode == 0
    assert json.loads(station.read_text())["z0_m"] == 3.1
    shown = run_tool("show", "--file", str(station))
    assert "3.1" in shown.stdout


def test_set_units_to_feet_scales_the_predicted_height(station):
    before = run_tool("predict", "--file", str(station), "--at", "2024-06-01 12:00")
    assert before.returncode == 0
    metres = float(before.stdout.split()[-2])

    changed = run_tool("set", "--file", str(station), "--units", "ft")
    assert changed.returncode == 0
    after = run_tool("predict", "--file", str(station), "--at", "2024-06-01 12:00")
    assert after.returncode == 0
    assert after.stdout.strip().endswith("ft")
    feet = float(after.stdout.split()[-2])
    assert feet == pytest.approx(metres * 3.28084, abs=0.005)


def test_set_rejects_an_unsupported_unit_as_a_usage_error(station):
    result = run_tool("set", "--file", str(station), "--units", "fathoms")
    assert result.returncode == 1
    assert result.stdout == ""
    assert "usage: tidegrid set" in result.stderr


def test_set_without_any_field_is_a_usage_error(station):
    result = run_tool("set", "--file", str(station))
    assert result.returncode == 1
    assert "nothing to change" in result.stderr


def test_set_name_and_datum_are_persisted(station):
    assert run_tool("set", "--file", str(station), "--name", "Port Z",
                    "--datum", "LAT").returncode == 0
    payload = json.loads(station.read_text())
    assert (payload["name"], payload["datum"]) == ("Port Z", "LAT")


# --- T7: the constant editor ---------------------------------------------------


def test_list_shows_amplitude_and_phase(station):
    result = run_tool("list", "--file", str(station))
    assert result.returncode == 0
    line = next(l for l in result.stdout.splitlines() if l.startswith("\u2502 M2"))
    assert "3.72" in line and "118.4" in line


def test_add_rejects_a_duplicate_and_keeps_the_stored_values(station):
    result = run_tool("add", "M2", "--file", str(station), "--amplitude", "0.10", "--phase", "5")
    assert result.returncode == 2
    assert "already present" in result.stderr
    stored = json.loads(station.read_text())["constituents"][0]
    assert stored == {"name": "M2", "amplitude_m": 3.72, "phase_deg": 118.4}


def test_add_validation_leaves_the_file_untouched(station):
    before = station.read_text()
    result = run_tool("add", "S2", "--file", str(station), "--amplitude", "abc", "--phase", "10")
    assert result.returncode == 2
    assert "non-negative number" in result.stderr
    assert station.read_text() == before


def test_add_then_list_then_remove_round_trip(station):
    add = run_tool("add", "M4", "--file", str(station), "--amplitude", "0.09", "--phase", "312.8")
    assert add.returncode == 0
    assert "M4" in run_tool("list", "--file", str(station)).stdout

    remove = run_tool("remove", "M4", "--file", str(station))
    assert remove.returncode == 0
    assert "M4" not in run_tool("list", "--file", str(station)).stdout


def test_remove_an_absent_constituent_fails(station):
    result = run_tool("remove", "M6", "--file", str(station))
    assert result.returncode == 2
    assert "not present" in result.stderr


def test_list_on_an_empty_station_points_at_add(tmp_path):
    path = tmp_path / "bare.json"
    run_tool("init", "--file", str(path), "--name", "Bare")
    result = run_tool("list", "--file", str(path))
    assert result.returncode == 0
    assert "no constituents yet" in result.stdout
    assert "tidegrid add M2" in result.stdout


# --- T8: the synthesis core ----------------------------------------------------


def test_z0_shifts_every_height_by_exactly_one_metre(station):
    before = run_tool("predict", "--file", str(station), "--at", "2024-06-01 00:00")
    assert run_tool("set", "--file", str(station), "--z0", "3.35").returncode == 0
    after = run_tool("predict", "--file", str(station), "--at", "2024-06-01 00:00")
    assert float(after.stdout.split()[-2]) - float(before.stdout.split()[-2]) == pytest.approx(
        1.0, abs=0.002
    )


def test_predict_at_prints_a_single_time_and_height_line(station):
    result = run_tool("predict", "--file", str(station), "--at", "2024-06-01 00:00")
    assert result.returncode == 0
    assert result.stdout.count("\n") == 1
    assert result.stdout.startswith("2024-06-01 00:00")


def test_predict_refuses_a_station_without_z0(station):
    payload = json.loads(station.read_text())
    del payload["z0_m"]
    station.write_text(json.dumps(payload))
    result = run_tool("predict", "--file", str(station), "--at", "2024-06-01 00:00")
    assert result.returncode == 2
    assert result.stdout == ""
    assert "z0_m" in result.stderr


def test_predict_refuses_a_station_without_constituents(tmp_path):
    path = tmp_path / "bare.json"
    run_tool("init", "--file", str(path), "--name", "Bare", "--z0", "1.5")
    result = run_tool("predict", "--file", str(path), "--date", "2024-06-01")
    assert result.returncode == 2
    assert "no constituents" in result.stderr


def test_synthesis_is_deterministic_and_uses_the_stored_z0():
    station = tidegrid.Station(
        name="Test",
        datum="MLLW",
        z0_m=2.5,
        units="m",
        timezone_offset_minutes=0,
        constituents=(tidegrid.ConstituentTerm("M2", 1.0, 0.0, 28.984104),),
        path=Path("station.json"),
    )
    instant = datetime(2024, 6, 1, 0, 0)
    # h(t) = Z0 + A*cos(omega*t) with t = 213168 hours from the epoch.
    hours = (instant - tidegrid.EPOCH_UTC).total_seconds() / 3600.0
    expected = 2.5 + 1.0 * __import__("math").cos(__import__("math").radians(28.984104 * hours))
    assert tidegrid.predict_height_m(station, instant) == pytest.approx(expected, abs=1e-9)
    assert tidegrid.predict_height_m(station, instant) == tidegrid.predict_height_m(
        station, instant
    )


def test_timezone_offset_moves_the_epoch_by_match():
    term = (tidegrid.ConstituentTerm("M2", 1.0, 0.0, 28.984104),)
    instant = datetime(2024, 6, 1, 0, 0)
    utc = tidegrid.Station("A", "MLLW", 0.0, "m", 0, term, Path("a.json"))
    plus_five = tidegrid.Station("B", "MLLW", 0.0, "m", 300, term, Path("b.json"))
    expected = tidegrid.predict_height_m(
        utc, instant - timedelta(hours=5)
    )
    assert tidegrid.predict_height_m(plus_five, instant) == pytest.approx(expected, abs=1e-9)


# --- T9: the high/low water scan ----------------------------------------------


def test_one_day_has_alternating_highs_and_lows(station):
    result = run_tool("predict", "--file", str(station), "--date", "2024-06-01")
    assert result.returncode == 0
    kinds = [line.split("\u2502")[2].strip() for line in result.stdout.splitlines()[3:-1]]
    assert len(kinds) >= 4
    assert kinds.count("high") >= 2 and kinds.count("low") >= 2
    assert all(kind in ("high", "low") for kind in kinds)
    assert all(first != second for first, second in zip(kinds, kinds[1:])), kinds


def test_three_day_range_is_chronological_without_duplicates(station):
    result = run_tool(
        "predict", "--file", str(station), "--from", "2024-06-01", "--to", "2024-06-03"
    )
    assert result.returncode == 0
    times = [
        datetime.strptime(line.split("\u2502")[1].strip(), "%Y-%m-%d %H:%M")
        for line in result.stdout.splitlines()[3:-1]
    ]
    assert len(times) >= 10
    assert times == sorted(times)
    assert len(set(times)) == len(times)
    assert {t.date() for t in times} == {
        datetime(2024, 6, 1).date(),
        datetime(2024, 6, 2).date(),
        datetime(2024, 6, 3).date(),
    }


def test_extrema_land_on_the_parabolic_scan_of_the_synthesis(station):
    parsed, problems = tidegrid.load_station(station)
    assert problems == ()
    start = datetime(2024, 6, 1)
    extrema = tidegrid.find_extrema(parsed, start, start + timedelta(days=1) - timedelta(minutes=1))
    assert len(extrema) >= 4
    for index, row in enumerate(extrema):
        assert abs(row.height_m - tidegrid.predict_height_m(parsed, row.when)) < 1e-9
        if index:
            previous = extrema[index - 1]
            assert previous.when < row.when
            assert previous.kind != row.kind
            if row.kind == "low":
                assert previous.height_m > row.height_m
            else:
                assert previous.height_m < row.height_m


def test_extrema_scan_is_fast(station):
    import time

    parsed, _ = tidegrid.load_station(station)
    start = datetime(2024, 6, 1)
    began = time.perf_counter()
    tidegrid.find_extrema(parsed, start, start + timedelta(days=1) - timedelta(minutes=1))
    assert time.perf_counter() - began < 1.0


# --- T10: export ---------------------------------------------------------------


def test_export_csv_writes_a_header_and_one_row_per_extremum(station, tmp_path):
    out = tmp_path / "tides.csv"
    result = run_tool(
        "export", "--file", str(station), "--from", "2024-06-01", "--to", "2024-06-03",
        "--format", "csv", "--out", str(out),
    )
    assert result.returncode == 0, result.stderr
    assert str(out) in result.stdout
    rows = list(csv.reader(io.StringIO(out.read_text())))
    assert rows[0] == ["time", "type", "height", "units"]
    assert len(rows) - 1 >= 10
    assert f"({len(rows) - 1} rows)" in result.stdout
    assert {"high", "low"} <= {row[1] for row in rows[1:]}
    assert all(row[3] == "m" for row in rows[1:])


def test_export_txt_streams_a_fixed_width_table(station):
    result = run_tool(
        "export", "--file", str(station), "--from", "2024-06-01", "--to", "2024-06-01",
        "--format", "txt",
    )
    assert result.returncode == 0
    assert result.stdout.startswith("\u250c")
    assert "high" in result.stdout and "low" in result.stdout


def test_export_matches_predict_for_the_same_instant(station, tmp_path):
    out = tmp_path / "tides.csv"
    run_tool("export", "--file", str(station), "--from", "2024-06-01", "--to", "2024-06-01",
             "--format", "csv", "--out", str(out))
    rows = list(csv.DictReader(io.StringIO(out.read_text())))
    assert rows
    for row in rows[:3]:
        predicted = run_tool("predict", "--file", str(station), "--at", row["time"])
        assert predicted.returncode == 0
        assert float(predicted.stdout.split()[-2]) == pytest.approx(float(row["height"]), abs=1e-9)


def test_export_to_stdout_is_pure_csv(station):
    result = run_tool(
        "export", "--file", str(station), "--from", "2024-06-01", "--to", "2024-06-01",
        "--format", "csv",
    )
    assert result.returncode == 0
    assert result.stdout.splitlines()[0] == "time,type,height,units"
    assert result.stderr == ""


def test_export_to_an_unwritable_path_fails_with_one_line(station, tmp_path):
    target = tmp_path / "no-such-dir" / "tides.csv"
    result = run_tool(
        "export", "--file", str(station), "--from", "2024-06-01", "--to", "2024-06-01",
        "--out", str(target),
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1


def test_export_rejects_an_unsupported_format(station):
    result = run_tool("export", "--file", str(station), "--from", "2024-06-01",
                      "--to", "2024-06-01", "--format", "pdf")
    assert result.returncode == 1
    assert "usage: tidegrid export" in result.stderr


# --- T11: input validation -----------------------------------------------------


def test_bad_calendar_date_is_a_data_error(station):
    result = run_tool("predict", "--file", str(station), "--date", "2024-13-45")
    assert result.returncode == 2
    assert result.stdout == ""
    assert "not a real calendar date" in result.stderr


def test_bad_instant_reports_the_expected_format(station):
    result = run_tool("predict", "--file", str(station), "--at", "not a date")
    assert result.returncode == 2
    assert "YYYY-MM-DD HH:MM" in result.stderr


def test_reversed_range_is_a_data_error(station):
    result = run_tool("predict", "--file", str(station), "--from", "2024-06-03",
                      "--to", "2024-06-01")
    assert result.returncode == 2
    assert "before the start" in result.stderr


def test_phase_out_of_range_is_a_data_error(station):
    result = run_tool("add", "M4", "--file", str(station), "--amplitude", "0.1", "--phase", "400")
    assert result.returncode == 2
    assert "between 0 and 360 degrees" in result.stderr


# --- T12: the shipped example station and the shipped transcript ----------------


def test_example_station_is_valid_and_seeded_with_real_constants():
    example = ROOT / "examples" / "port-haven.json"
    result = run_tool("validate", "--file", str(example))
    assert result.returncode == 0, result.stderr
    payload = json.loads(example.read_text())
    assert payload["z0_m"] > 0
    assert len(payload["constituents"]) >= 5


def test_example_station_predicts_a_full_day_of_waters():
    example = ROOT / "examples" / "port-haven.json"
    result = run_tool("predict", "--file", str(example), "--date", "2024-06-01")
    assert result.returncode == 0
    assert result.stdout.count("high") >= 2 and result.stdout.count("low") >= 2


def test_console_entry_point_is_declared_for_the_root_module():
    text = (ROOT / "pyproject.toml").read_text()
    assert 'tidegrid = "tidegrid:main"' in text
    assert 'py-modules = ["tidegrid"]' in text


def test_tool_runs_as_a_module_too():
    result = subprocess.run(
        [sys.executable, "-m", "tidegrid", "--version"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=60,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f"tidegrid {tidegrid.VERSION}"
