"""Shared fixtures: the tool under test and a station file built through its own CLI."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tidegrid.py"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONSTANTS = (
    ("M2", "3.72", "118.4"),
    ("S2", "0.63", "145.2"),
    ("N2", "0.72", "96.7"),
    ("K1", "0.11", "275.3"),
    ("[redacted]", "0.09", "258.6"),
)


def run_tool(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run the CLI exactly as a user would, capturing both streams."""
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else str(ROOT),
        timeout=60,
    )


@pytest.fixture(scope="session")
def station_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One station built once through the CLI: init plus five catalogue constants."""
    path = tmp_path_factory.mktemp("station") / "station.json"
    init = run_tool(
        "init",
        "--file",
        str(path),
        "--name",
        "Port Haven",
        "--datum",
        "MLLW",
        "--z0",
        "2.35",
        "--tz-offset",
        "-300",
    )
    assert init.returncode == 0, init.stderr
    for symbol, amplitude, phase in CONSTANTS:
        added = run_tool(
            "add", symbol, "--file", str(path), "--amplitude", amplitude, "--phase", phase
        )
        assert added.returncode == 0, added.stderr
    return path


@pytest.fixture()
def station(tmp_path: Path, station_template: Path) -> Path:
    """A private, writable copy of that station, so mutating tests cannot interfere."""
    path = tmp_path / "station.json"
    shutil.copyfile(station_template, path)
    return path
