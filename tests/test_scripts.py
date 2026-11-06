"""The example scripts, run exactly as README.md documents them.

These keep the documented commands working: each one must succeed, print what the README says
it prints, and compute the same chart as the engine does for the same record.
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path

from astro_research.astronomical_time import TimeScale
from astro_research.chart import BirthRecord, ChartSettings, calculation_key
from astro_research.civil_time import ZonedLocalTime
from astro_research.ephemeris import Ephemeris
from astro_research.geo import GeoPosition
from astro_research.measurements import MAJOR, OCTILE

REPO = Path(__file__).resolve().parents[1]
CHART = [
    "scripts/chart.py",
    "--local", "1987-06-15T04:25",
    "--zone", "Europe/London",
    "--lat", "51.5074",
    "--lon", "-0.1278",
    "--time-scale", "clock-as-ut1",
    "--aspects", "major,octile",
]  # fmt: skip
COMPARE = [
    "scripts/compare.py",
    "--a-local", "1987-06-15T04:25", "--a-zone", "Europe/London",
    "--a-lat", "51.5074", "--a-lon", "-0.1278",
    "--b-local", "1987-06-15T04:26", "--b-zone", "Europe/London",
    "--b-lat", "51.5074", "--b-lon", "-0.1278",
    "--time-scale", "clock-as-ut1",
    "--aspects", "major,octile",
]  # fmt: skip


def run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *arguments], cwd=REPO, capture_output=True, text=True, check=False
    )


def test_the_chart_script_prints_the_engine_s_chart(fetched_ephe: Path) -> None:
    result = run(CHART)
    assert result.returncode == 0, result.stderr
    for heading in ("Input", "Time", "Positions", "Angles", "Pairs"):
        assert f"== {heading}" in result.stdout
    for name in ("sun", "pluto", "true_node", "osculating_apogee", "ascendant", "vertex"):
        assert name in result.stdout
    assert "1987-06-15 03:25:00 UTC" in result.stdout
    # The same record and settings through the engine give the key the script printed.
    record = BirthRecord(
        time=ZonedLocalTime(local=datetime(1987, 6, 15, 4, 25), zone="Europe/London"),
        place=GeoPosition(latitude=51.5074, longitude=-0.1278),
    )
    settings = ChartSettings(
        time_scale=TimeScale.CLOCK_AS_UT1, aspect_angles=tuple(sorted({*MAJOR, *OCTILE}))
    )
    key = calculation_key(record, settings, Ephemeris.open(fetched_ephe).provenance)
    assert f"calculation key  {key}" in result.stdout


def test_the_compare_script_prints_b_minus_a(fetched_ephe: Path) -> None:
    result = run(COMPARE)
    assert result.returncode == 0, result.stderr
    for heading in ("Time", "Points", "Angle contacts"):
        assert f"== {heading}" in result.stdout
    assert "utc seconds  60.0" in result.stdout
    assert "ascendant" in result.stdout


def test_a_refused_record_is_an_error_not_a_guess() -> None:
    # 01:30 on 1987-10-25 happened twice in London; the script must not pick one.
    arguments = [*CHART]
    arguments[arguments.index("1987-06-15T04:25")] = "1987-10-25T01:30"
    result = run(arguments)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "AmbiguousLocalTime" in result.stderr


def test_the_time_scale_and_aspects_must_be_named() -> None:
    for option in ("--time-scale", "--aspects"):
        arguments = [*CHART]
        i = arguments.index(option)
        del arguments[i : i + 2]
        result = run(arguments)
        assert result.returncode == 2
        assert option in result.stderr
