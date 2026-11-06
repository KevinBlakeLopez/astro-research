"""Birth records, chart settings, whole charts and their calculation keys.

A chart is everything Phase 1 computes for one birth record under named settings: the resolved
time, positions, angles, chart points and pair measurements. Its calculation key is a SHA-256 of
everything that went in, so identical inputs give identical keys in any process.

The records here are synthetic; no real person's birth data is used as a fixture.
"""

import hashlib
import importlib.metadata
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from astro_research.angles import ANGLE_CONVENTIONS, AnglesUndefined
from astro_research.astronomical_time import TimeScale
from astro_research.chart import (
    BirthRecord,
    ChartSettings,
    InvalidBirthRecord,
    InvalidChartSettings,
    TimeResolution,
    calculation_inputs,
    calculation_key,
    compute_chart,
)
from astro_research.civil_time import (
    TZDATA_VERSION,
    AmbiguousLocalTime,
    CivilTime,
    LocalMeanTime,
    OffsetLocalTime,
    UtcTime,
    ZonedLocalTime,
    resolve,
)
from astro_research.ephemeris import Ephemeris, EphemerisProvenance
from astro_research.geo import GeoPosition
from astro_research.measurements import (
    MAJOR,
    MEASUREMENT_CONVENTIONS,
    OCTILE,
    ChartAngle,
    chart_points,
    measure,
)
from astro_research.positions import ALL_POINTS, POSITION_CONVENTIONS, Body, LunarPoint

TESTS = Path(__file__).resolve().parent
LONDON = GeoPosition(latitude=51.5074, longitude=-0.1278)
RECORD = BirthRecord(
    time=ZonedLocalTime(local=datetime(1987, 6, 15, 4, 25), zone="Europe/London"),
    place=LONDON,
    time_resolution=TimeResolution.MINUTE,
)
ASPECTS = tuple(sorted({*MAJOR, *OCTILE}))  # MAJOR + OCTILE would repeat 90 and 180
SETTINGS = ChartSettings(time_scale=TimeScale.CLOCK_AS_UT1, aspect_angles=ASPECTS)
# Made-up provenance for key tests that need no ephemeris files.
PROVENANCE = EphemerisProvenance(
    binding="pysweph 2.10.3.6",
    swisseph_version="2.10.03",
    de_number=441,
    source_commit="c" * 40,
    file_sha256=(("sepl_18.se1", "a" * 64), ("semo_18.se1", "b" * 64)),
)


@pytest.fixture
def eph(fetched_ephe: Path) -> Ephemeris:
    return Ephemeris.open(fetched_ephe)


# --- Birth records --------------------------------------------------------------------------


def test_a_record_needs_only_a_time_and_a_place() -> None:
    record = BirthRecord(time=UtcTime(utc=datetime(2000, 1, 1, 12, tzinfo=UTC)), place=LONDON)
    assert record.time_resolution is None
    assert record.stated_uncertainty is None


def test_a_record_holds_no_names_or_identifiers() -> None:
    # Subjects, sources and dataset identifiers are separate concepts, kept out of the
    # calculation layer together with any personal data they carry.
    assert [f.name for f in fields(BirthRecord)] == [
        "time",
        "place",
        "time_resolution",
        "stated_uncertainty",
    ]


@pytest.mark.parametrize("uncertainty", [timedelta(0), timedelta(minutes=-5)])
def test_a_stated_uncertainty_must_be_positive(uncertainty: timedelta) -> None:
    with pytest.raises(InvalidBirthRecord, match="uncertainty"):
        replace(RECORD, stated_uncertainty=uncertainty)


@pytest.mark.parametrize(
    ("time", "resolution"),
    [
        (ZonedLocalTime(local=datetime(1987, 6, 15, 4, 25, 30), zone="Europe/London"), "MINUTE"),
        (ZonedLocalTime(local=datetime(1987, 6, 15, 4, 25), zone="Europe/London"), "HOUR"),
        (
            OffsetLocalTime(
                local=datetime(1987, 6, 15, 4, 25, 30, 500_000), utc_offset=timedelta(hours=1)
            ),
            "SECOND",
        ),
        (UtcTime(utc=datetime(1987, 6, 15, 3, 25, 30, tzinfo=UTC)), "MINUTE"),
        (LocalMeanTime(local=datetime(1987, 6, 15, 4, 0, 1)), "HOUR"),
    ],
)
def test_the_clock_reading_must_fit_its_stated_resolution(time: CivilTime, resolution: str) -> None:
    # A reading of 04:25:30 recorded to the minute contradicts itself; it is refused, not rounded.
    with pytest.raises(InvalidBirthRecord, match="resolution"):
        BirthRecord(time=time, place=LONDON, time_resolution=TimeResolution[resolution])


@pytest.mark.parametrize(
    ("time", "resolution"),
    [
        (ZonedLocalTime(local=datetime(1987, 6, 15, 4, 0), zone="Europe/London"), "HOUR"),
        (ZonedLocalTime(local=datetime(1987, 6, 15, 4, 25), zone="Europe/London"), "MINUTE"),
        (UtcTime(utc=datetime(1987, 6, 15, 3, 25, 30, tzinfo=UTC)), "SECOND"),
        (LocalMeanTime(local=datetime(1987, 6, 15, 4, 0)), "MINUTE"),
    ],
)
def test_a_reading_at_its_resolution_is_accepted(time: CivilTime, resolution: str) -> None:
    record = BirthRecord(time=time, place=LONDON, time_resolution=TimeResolution[resolution])
    assert record.time_resolution is TimeResolution[resolution]


# --- Chart settings -------------------------------------------------------------------------


def test_time_scale_and_aspect_angles_must_be_named() -> None:
    # The engine chooses neither: the time-scale default is undecided, and aspect angles are
    # the researcher's choice.
    with pytest.raises(TypeError):
        ChartSettings(aspect_angles=MAJOR)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        ChartSettings(time_scale=TimeScale.CLOCK_AS_UT1)  # type: ignore[call-arg]


def test_the_time_scale_must_be_a_time_scale() -> None:
    with pytest.raises(InvalidChartSettings, match="time scale"):
        ChartSettings(time_scale="clock_as_ut1", aspect_angles=MAJOR)  # type: ignore[arg-type]


def test_points_default_to_every_body_and_lunar_point() -> None:
    assert SETTINGS.points == ALL_POINTS


def test_settings_are_stored_as_tuples_of_floats() -> None:
    settings = ChartSettings(
        time_scale=TimeScale.CLOCK_AS_UT1,
        aspect_angles=[0, 90],  # type: ignore[arg-type]
        points=[Body.SUN, Body.MOON],  # type: ignore[arg-type]
    )
    assert settings.aspect_angles == (0.0, 90.0)
    assert all(type(angle) is float for angle in settings.aspect_angles)
    assert settings.points == (Body.SUN, Body.MOON)
    assert settings == ChartSettings(
        time_scale=TimeScale.CLOCK_AS_UT1, aspect_angles=(0.0, 90.0), points=(Body.SUN, Body.MOON)
    )


@pytest.mark.parametrize(
    "angles",
    [MAJOR + OCTILE, (-1.0,), (180.5,), (math.nan,)],
    ids=["repeated", "negative", "beyond-180", "nan"],
)
def test_aspect_angles_must_be_distinct_and_within_0_to_180(angles: tuple[float, ...]) -> None:
    with pytest.raises(InvalidChartSettings, match="aspect angles"):
        ChartSettings(time_scale=TimeScale.CLOCK_AS_UT1, aspect_angles=angles)


@pytest.mark.parametrize(
    "points",
    [(Body.SUN, Body.SUN), (Body.SUN, ChartAngle.ASCENDANT)],
    ids=["repeated", "an-angle"],
)
def test_points_must_be_distinct_bodies_or_lunar_points(points: tuple[object, ...]) -> None:
    # The Ascendant, MC and Vertex are part of every chart and are not chosen here.
    with pytest.raises(InvalidChartSettings, match="points"):
        ChartSettings(
            time_scale=TimeScale.CLOCK_AS_UT1,
            aspect_angles=MAJOR,
            points=points,  # type: ignore[arg-type]
        )


# --- Computing a chart ----------------------------------------------------------------------


def test_a_chart_is_the_engine_run_on_the_record(eph: Ephemeris) -> None:
    chart = compute_chart(eph, RECORD, SETTINGS)
    time = eph.time(resolve(RECORD.time, RECORD.place), SETTINGS.time_scale)
    positions = eph.positions(time, SETTINGS.points)
    angles = eph.angles(time, RECORD.place)
    points = chart_points(positions, angles)
    assert chart.record == RECORD
    assert chart.settings == SETTINGS
    assert chart.time == time
    assert chart.positions == positions
    assert chart.angles == angles
    assert chart.points == points
    assert chart.pairs == measure(points, SETTINGS.aspect_angles)
    assert chart.measurement_conventions == MEASUREMENT_CONVENTIONS


def test_a_chart_keeps_the_original_input(eph: Ephemeris) -> None:
    chart = compute_chart(eph, RECORD, SETTINGS)
    assert chart.time.instant.input is RECORD.time
    assert chart.time.instant.utc == datetime(1987, 6, 15, 3, 25, tzinfo=UTC)  # BST = UTC+1


def test_chosen_points_come_first_then_the_three_angles(eph: Ephemeris) -> None:
    settings = replace(SETTINGS, points=(Body.MOON, LunarPoint.TRUE_NODE))
    chart = compute_chart(eph, RECORD, settings)
    assert [p.point for p in chart.points] == [
        Body.MOON,
        LunarPoint.TRUE_NODE,
        ChartAngle.ASCENDANT,
        ChartAngle.MIDHEAVEN,
        ChartAngle.VERTEX,
    ]
    assert len(chart.pairs) == 10


def test_computing_twice_gives_equal_charts(eph: Ephemeris) -> None:
    assert compute_chart(eph, RECORD, SETTINGS) == compute_chart(eph, RECORD, SETTINGS)


def test_an_ambiguous_local_time_is_refused_not_guessed(eph: Ephemeris) -> None:
    # British Summer Time ended at 02:00 on 1987-10-25, so 01:30 happened twice.
    record = BirthRecord(
        time=ZonedLocalTime(local=datetime(1987, 10, 25, 1, 30), zone="Europe/London"),
        place=LONDON,
    )
    with pytest.raises(AmbiguousLocalTime):
        compute_chart(eph, record, SETTINGS)


def test_a_chart_at_a_pole_is_refused(eph: Ephemeris) -> None:
    record = replace(RECORD, place=GeoPosition(latitude=90.0, longitude=0.0))
    with pytest.raises(AnglesUndefined):
        compute_chart(eph, record, SETTINGS)


# --- Calculation key ------------------------------------------------------------------------


def test_the_key_is_the_sha256_of_the_canonical_inputs() -> None:
    key = calculation_key(RECORD, SETTINGS, PROVENANCE)
    assert re.fullmatch("[0-9a-f]{64}", key)
    inputs = calculation_inputs(RECORD, SETTINGS, PROVENANCE)
    assert key == hashlib.sha256(inputs.encode()).hexdigest()


def test_the_canonical_inputs_are_sorted_compact_json() -> None:
    inputs = calculation_inputs(RECORD, SETTINGS, PROVENANCE)
    assert inputs == json.dumps(json.loads(inputs), sort_keys=True, separators=(",", ":"))


def test_the_inputs_spell_out_the_record_settings_and_ephemeris() -> None:
    inputs = json.loads(calculation_inputs(RECORD, SETTINGS, PROVENANCE))
    assert inputs["record"] == {
        "time": {
            "kind": "zoned_local_time",
            "local": "1987-06-15T04:25:00",
            "zone": "Europe/London",
            "stated_utc_offset_us": None,
            "accept_pre_1970_tzdb": False,
        },
        "place": {"latitude": 51.5074, "longitude": -0.1278, "elevation_m": 0.0},
        "time_resolution": "minute",
        "stated_uncertainty_us": None,
    }
    assert inputs["settings"] == {
        "time_scale": "clock_as_ut1",
        "aspect_angles": [0.0, 45.0, 60.0, 90.0, 120.0, 135.0, 180.0],
        "points": [p.value for p in ALL_POINTS],
    }
    assert inputs["ephemeris"] == {
        "binding": "pysweph 2.10.3.6",
        "swisseph_version": "2.10.03",
        "de_number": 441,
        "source_commit": "c" * 40,
        "file_sha256": [["sepl_18.se1", "a" * 64], ["semo_18.se1", "b" * 64]],
    }


def test_offsets_and_uncertainties_are_written_in_whole_microseconds() -> None:
    record = BirthRecord(
        time=OffsetLocalTime(local=datetime(1987, 6, 15, 4, 25), utc_offset=timedelta(hours=1)),
        place=LONDON,
        stated_uncertainty=timedelta(minutes=5),
    )
    inputs = json.loads(calculation_inputs(record, SETTINGS, PROVENANCE))
    assert inputs["record"]["time"] == {
        "kind": "offset_local_time",
        "local": "1987-06-15T04:25:00",
        "utc_offset_us": 3_600_000_000,
    }
    assert inputs["record"]["stated_uncertainty_us"] == 300_000_000


def test_the_inputs_name_the_engine_version_tzdata_and_conventions() -> None:
    inputs = json.loads(calculation_inputs(RECORD, SETTINGS, PROVENANCE))
    assert inputs["engine"] == {
        "package": "astro-research",
        "version": importlib.metadata.version("astro-research"),
    }
    assert inputs["tzdata"] == TZDATA_VERSION
    assert inputs["conventions"] == {
        "positions": POSITION_CONVENTIONS,
        "angles": ANGLE_CONVENTIONS,
        "measurements": MEASUREMENT_CONVENTIONS,
    }


def test_equal_inputs_written_differently_give_the_same_key() -> None:
    as_ints = replace(SETTINGS, aspect_angles=tuple(int(angle) for angle in ASPECTS))
    assert calculation_key(RECORD, as_ints, PROVENANCE) == calculation_key(
        RECORD, SETTINGS, PROVENANCE
    )
    utc_keys = {
        calculation_key(
            BirthRecord(time=UtcTime(utc=datetime(1987, 6, 15, 3, 25, tzinfo=tz)), place=LONDON),
            SETTINGS,
            PROVENANCE,
        )
        for tz in (UTC, timezone(timedelta(0)), ZoneInfo("Etc/UTC"))
    }
    assert len(utc_keys) == 1
    at_zero = replace(RECORD, place=replace(LONDON, longitude=0.0))
    at_minus_zero = replace(RECORD, place=replace(LONDON, longitude=-0.0))
    assert calculation_key(at_minus_zero, SETTINGS, PROVENANCE) == calculation_key(
        at_zero, SETTINGS, PROVENANCE
    )


def _single_changes() -> list[tuple[BirthRecord, ChartSettings, EphemerisProvenance]]:
    zoned = RECORD.time
    assert isinstance(zoned, ZonedLocalTime)
    records = [
        replace(RECORD, time=replace(zoned, local=zoned.local + timedelta(minutes=1))),
        replace(RECORD, time=replace(zoned, zone="Europe/Dublin")),  # same offset, other zone
        replace(RECORD, time=replace(zoned, stated_utc_offset=timedelta(hours=1))),
        replace(RECORD, time=replace(zoned, accept_pre_1970_tzdb=True)),
        # The same instant written as other kinds of input:
        replace(RECORD, time=OffsetLocalTime(local=zoned.local, utc_offset=timedelta(hours=1))),
        replace(RECORD, time=UtcTime(utc=datetime(1987, 6, 15, 3, 25, tzinfo=UTC))),
        replace(RECORD, place=replace(LONDON, latitude=51.50741)),
        replace(RECORD, place=replace(LONDON, longitude=-0.12781)),
        replace(RECORD, place=replace(LONDON, elevation_m=11.0)),
        replace(RECORD, time_resolution=None),
        replace(RECORD, time_resolution=TimeResolution.SECOND),
        replace(RECORD, stated_uncertainty=timedelta(minutes=5)),
    ]
    settings = [
        replace(SETTINGS, time_scale=TimeScale.UT1_FROM_DELTA_T),
        replace(SETTINGS, aspect_angles=MAJOR),
        replace(SETTINGS, aspect_angles=tuple(reversed(ASPECTS))),
        replace(SETTINGS, points=ALL_POINTS[:-1]),
        replace(SETTINGS, points=tuple(reversed(ALL_POINTS))),
    ]
    provenances = [
        replace(PROVENANCE, binding="pysweph 2.10.3.7"),
        replace(PROVENANCE, swisseph_version="2.10.04"),
        replace(PROVENANCE, de_number=431),
        replace(PROVENANCE, source_commit="d" * 40),
        replace(PROVENANCE, file_sha256=(("sepl_18.se1", "a" * 64), ("semo_18.se1", "e" * 64))),
    ]
    return (
        [(RECORD, SETTINGS, PROVENANCE)]
        + [(record, SETTINGS, PROVENANCE) for record in records]
        + [(RECORD, setting, PROVENANCE) for setting in settings]
        + [(RECORD, SETTINGS, provenance) for provenance in provenances]
    )


def test_any_single_change_gives_a_new_key() -> None:
    variants = _single_changes()
    keys = [calculation_key(*variant) for variant in variants]
    assert len(set(keys)) == len(keys) == 23


def test_a_microsecond_changes_the_key() -> None:
    # Subsecond precision is kept; a record with no stated resolution may carry it.
    zoned = RECORD.time
    assert isinstance(zoned, ZonedLocalTime)
    exact = replace(RECORD, time_resolution=None)
    later = replace(exact, time=replace(zoned, local=zoned.local + timedelta(microseconds=1)))
    assert calculation_key(later, SETTINGS, PROVENANCE) != calculation_key(
        exact, SETTINGS, PROVENANCE
    )


def test_a_field_the_key_does_not_encode_is_refused() -> None:
    # A field added to a record later must be added to the key too, or the key would ignore it.
    @dataclass(frozen=True, kw_only=True)
    class ExtendedRecord(BirthRecord):
        extra: str = "anything"

    extended = ExtendedRecord(time=RECORD.time, place=RECORD.place)
    with pytest.raises(TypeError, match="extra"):
        calculation_key(extended, SETTINGS, PROVENANCE)


def test_a_chart_carries_the_key_of_its_inputs(eph: Ephemeris) -> None:
    chart = compute_chart(eph, RECORD, SETTINGS)
    assert chart.calculation_key == calculation_key(RECORD, SETTINGS, eph.provenance)


@pytest.mark.parametrize("hash_seed", ["0", "4242"])
def test_the_key_is_the_same_in_a_fresh_process(hash_seed: str) -> None:
    # Nothing in the key may depend on the per-process string hash seed (set order, say).
    script = (
        "import sys; sys.path.insert(0, sys.argv[1]); import test_chart as t; "
        "from astro_research.chart import calculation_key; "
        "print(calculation_key(t.RECORD, t.SETTINGS, t.PROVENANCE))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(TESTS)],
        env={**os.environ, "PYTHONHASHSEED": hash_seed},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == calculation_key(RECORD, SETTINGS, PROVENANCE)
