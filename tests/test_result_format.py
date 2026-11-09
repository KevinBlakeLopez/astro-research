"""Charts as JSON documents, read back exactly.

A document writes each value once: the calculation inputs verbatim (so their SHA-256 must match
the stored calculation key), then the time, the body positions, the angles, the chart points,
and the pairs, which name their points. Reading is strict. It refuses an unknown format, a
missing or extra field, a value of the wrong type, a structure the engine never produces, and
inputs that don't match the key. It doesn't compare the chart with today's engine: a chart
from another version is read as it was written, and its inputs say which version that was.

The records here are synthetic; no real person's birth data is used as a fixture.
"""

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from astro_research.angles import ANGLE_CONVENTIONS, Angles
from astro_research.astronomical_time import AstronomicalTime, TimeScale
from astro_research.chart import (
    BirthRecord,
    Chart,
    ChartSettings,
    TimeResolution,
    calculation_inputs,
    compute_chart,
)
from astro_research.civil_time import LocalMeanTime, OffsetLocalTime, UtcTime, ZonedLocalTime
from astro_research.ephemeris import Ephemeris
from astro_research.geo import GeoPosition
from astro_research.measurements import MAJOR, MEASUREMENT_CONVENTIONS, OCTILE, PairMeasurement
from astro_research.positions import POSITION_CONVENTIONS, Body, LunarPoint
from astro_research.result_format import (
    CHART_FORMAT,
    InvalidChartDocument,
    chart_from_json,
    chart_to_json,
)

ASPECTS = tuple(sorted({*MAJOR, *OCTILE}))
CASES = {
    # The default scale, IERS's UT1, with a stated resolution.
    "zoned-1987": (
        BirthRecord(
            time=ZonedLocalTime(local=datetime(1987, 6, 15, 4, 25), zone="Europe/London"),
            place=GeoPosition(latitude=51.5074, longitude=-0.1278),
            time_resolution=TimeResolution.MINUTE,
        ),
        ChartSettings(aspect_angles=ASPECTS),
    ),
    # A UTC reading to the microsecond, a stated uncertainty, the clock convention.
    "utc-2024": (
        BirthRecord(
            time=UtcTime(utc=datetime(2024, 3, 20, 12, 0, 0, 123456, tzinfo=UTC)),
            place=GeoPosition(latitude=-33.8688, longitude=151.2093),
            stated_uncertainty=timedelta(seconds=90),
        ),
        ChartSettings(aspect_angles=(90.0,), time_scale=TimeScale.CLOCK_AS_UT1),
    ),
    # A stated offset, the delta-T model, a few points only.
    "offset-1972": (
        BirthRecord(
            time=OffsetLocalTime(
                local=datetime(1972, 6, 30, 23, 30), utc_offset=timedelta(hours=5, minutes=30)
            ),
            place=GeoPosition(latitude=19.0760, longitude=72.8777),
        ),
        ChartSettings(
            aspect_angles=(45.0, 135.0),
            time_scale=TimeScale.UT1_FROM_DELTA_T,
            points=(Body.SUN, Body.MOON, LunarPoint.TRUE_NODE),
        ),
    ),
    # Local mean time before 1972, beyond the polar circle, with an elevation.
    "lmt-1850": (
        BirthRecord(
            time=LocalMeanTime(local=datetime(1850, 3, 1, 6, 0)),
            place=GeoPosition(latitude=69.6492, longitude=18.9553, elevation_m=10.0),
        ),
        ChartSettings(aspect_angles=(0.0, 180.0)),
    ),
    # A zone's pre-1970 history, accepted explicitly.
    "zoned-1955": (
        BirthRecord(
            time=ZonedLocalTime(
                local=datetime(1955, 7, 4, 12, 0),
                zone="America/New_York",
                accept_pre_1970_tzdb=True,
            ),
            place=GeoPosition(latitude=40.7128, longitude=-74.0060),
        ),
        ChartSettings(aspect_angles=MAJOR),
    ),
}


@pytest.fixture(scope="module")
def eph(fetched_ephe: Path) -> Ephemeris:
    return Ephemeris.open(fetched_ephe)


@pytest.fixture(scope="module", params=list(CASES))
def chart(request: pytest.FixtureRequest, eph: Ephemeris) -> Chart:
    record, settings = CASES[request.param]
    return compute_chart(eph, record, settings)


@pytest.fixture(scope="module")
def document(eph: Ephemeris) -> dict[str, Any]:
    record, settings = CASES["zoned-1987"]
    parsed: dict[str, Any] = json.loads(chart_to_json(compute_chart(eph, record, settings)))
    return parsed


# --- Reading back ---------------------------------------------------------------------------


def test_a_chart_reads_back_equal(chart: Chart) -> None:
    assert chart_from_json(chart_to_json(chart)) == chart


def test_writing_what_was_read_gives_the_same_text(chart: Chart) -> None:
    # Floats are written as Python's shortest repr, which reads back bit for bit (observed for
    # all 5,565 of a full chart), so the second writing matches the first exactly.
    text = chart_to_json(chart)
    assert chart_to_json(chart_from_json(text)) == text


def test_a_negative_zero_survives(chart: Chart) -> None:
    pair = replace(chart.pairs[0], separation_rate=-0.0)
    back = chart_from_json(chart_to_json(replace(chart, pairs=(pair, *chart.pairs[1:]))))
    assert math.copysign(1.0, back.pairs[0].separation_rate) == -1.0


# --- The document ---------------------------------------------------------------------------


def test_the_document_is_compact_json_led_by_its_format_and_key(chart: Chart) -> None:
    text = chart_to_json(chart)
    doc = json.loads(text)
    assert text == json.dumps(doc, separators=(",", ":"))
    sections = ["format", "calculation_key", "inputs", "time", "bodies", "angles", "points"]
    assert list(doc) == [*sections, "pairs"]
    assert doc["format"] == CHART_FORMAT == "chart-result-v1"
    assert doc["calculation_key"] == chart.calculation_key
    provenance = chart.positions.ephemeris
    assert doc["inputs"] == json.loads(calculation_inputs(chart.record, chart.settings, provenance))


def test_each_value_is_written_once(chart: Chart) -> None:
    text = chart_to_json(chart)
    assert text.count('"jd_tt"') == 1
    assert text.count('"swisseph_version"') == 1
    for conventions in (POSITION_CONVENTIONS, ANGLE_CONVENTIONS, MEASUREMENT_CONVENTIONS):
        assert text.count(json.dumps(conventions)) == 1  # inside the inputs only
    doc = json.loads(text)
    assert [point["point"] for point in doc["points"]] == [p.point.value for p in chart.points]
    first = doc["pairs"][0]
    assert (first["a"], first["b"]) == (chart.pairs[0].a.point.value, chart.pairs[0].b.point.value)
    deviation = first["deviations"][0]
    assert list(deviation) == ["angle", "deviation", "deviation_rate"]


def test_the_time_carries_its_utc_reading_and_where_its_ut1_came_from(chart: Chart) -> None:
    time = json.loads(chart_to_json(chart))["time"]
    assert time["utc"] == chart.time.instant.utc.isoformat()
    assert time["utc_offset_us"] == chart.time.instant.utc_offset // timedelta(microseconds=1)
    assert time["time_scale"] == chart.settings.time_scale.value
    assert time["ut1_basis"] == chart.time.ut1_basis.value


# --- Strict reading -------------------------------------------------------------------------


def _key_of(doc: dict[str, Any]) -> None:
    doc["inputs"]["record"]["place"]["latitude"] = 51.5


def _rekeyed(edit: Callable[[dict[str, Any]], object]) -> Callable[[dict[str, Any]], None]:
    """`edit` to the inputs, with the key recomputed so the edit gets past the key check."""

    def edited(doc: dict[str, Any]) -> None:
        edit(doc["inputs"])
        canonical = json.dumps(doc["inputs"], sort_keys=True, separators=(",", ":"))
        doc["calculation_key"] = hashlib.sha256(canonical.encode()).hexdigest()

    return edited


EDITS: dict[str, Callable[[dict[str, Any]], object]] = {
    "a time scale other than the settings'": lambda d: d["time"].update(time_scale="clock_as_ut1"),
    "an unknown key schema": _rekeyed(lambda i: i.update(schema="chart-key-v2")),
    "an extra input": _rekeyed(lambda i: i["record"].update(subject="anonymous")),
    "a place the engine refuses": _rekeyed(lambda i: i["record"]["place"].update(latitude=95.0)),
    "an unknown format": lambda d: d.update(format="chart-result-v2"),
    "a missing section": lambda d: d.pop("bodies"),
    "a missing field": lambda d: d["pairs"][0].pop("separation"),
    "an extra field": lambda d: d["angles"].update(house_system="placidus"),
    "a string for a number": lambda d: d["time"].update(jd_tt="2446961.6"),
    "an integer for a float": lambda d: d["points"][0].update(longitude=83),
    "a boolean for a number": lambda d: d["bodies"][0].update(distance_au=True),
    "a number for a boolean": lambda d: d["angles"].update(beyond_polar_circle=0),
    "an unknown name": lambda d: d["time"].update(ut1_basis="guessed"),
    "a pair naming an unknown point": lambda d: d["pairs"][0].update(a="chiron"),
    "a point that disagrees with its body": lambda d: d["points"][0].update(longitude=1.5),
    "a point left out with its pairs": lambda d: (
        d["points"].pop(),
        d.update(pairs=[p for p in d["pairs"] if "vertex" not in (p["a"], p["b"])]),
    ),
    "a pair left out": lambda d: d["pairs"].pop(),
    "pairs out of order": lambda d: d["pairs"].reverse(),
    "points out of order": lambda d: d["points"].reverse(),
    "a body the settings don't name": lambda d: d["bodies"].pop(),
    "a deviation for an angle not chosen": lambda d: d["pairs"][0]["deviations"][0].update(
        angle=30.0
    ),
    "inputs that don't match the key": _key_of,
}


@pytest.mark.parametrize("edit", list(EDITS))
def test_a_document_the_engine_did_not_write_is_refused(
    document: dict[str, Any], edit: str
) -> None:
    doc = json.loads(json.dumps(document))
    EDITS[edit](doc)
    with pytest.raises(InvalidChartDocument):
        chart_from_json(json.dumps(doc))


def test_a_pair_naming_a_point_the_settings_leave_out_is_refused(eph: Ephemeris) -> None:
    record, settings = CASES["offset-1972"]  # the Sun, the Moon and the true node only
    doc = json.loads(chart_to_json(compute_chart(eph, record, settings)))
    doc["pairs"][0]["a"] = "mars"
    with pytest.raises(InvalidChartDocument, match="mars"):
        chart_from_json(json.dumps(doc))


def test_edited_inputs_are_refused_by_the_key(document: dict[str, Any]) -> None:
    doc = json.loads(json.dumps(document))
    _key_of(doc)
    with pytest.raises(InvalidChartDocument, match="calculation key"):
        chart_from_json(json.dumps(doc))


@pytest.mark.parametrize("text", ["", "{", "[]", '"chart"', "null"])
def test_text_that_is_not_a_json_object_is_refused(text: str) -> None:
    with pytest.raises(InvalidChartDocument):
        chart_from_json(text)


def test_a_refusal_is_a_value_error() -> None:
    assert issubclass(InvalidChartDocument, ValueError)


# --- Writing --------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ExtendedAngles(Angles):
    house_system: str = "placidus"


@dataclass(frozen=True, kw_only=True)
class ExtendedTime(AstronomicalTime):
    leap_seconds: int = 37


@dataclass(frozen=True, kw_only=True)
class ExtendedPair(PairMeasurement):
    orb: float = 8.0


def _extended(obj: Any, cls: type) -> Any:
    return cls(**{field.name: getattr(obj, field.name) for field in fields(obj)})


@pytest.mark.parametrize(
    "copy", ["calculation key", "angles' time", "pair's point", "body's point"]
)
def test_a_chart_whose_copies_disagree_is_not_written(eph: Ephemeris, copy: str) -> None:
    # Each value is written once, so a second copy that differs would be lost without a trace.
    # The engine never builds such a chart; one made by hand is refused.
    record, settings = CASES["zoned-1987"]
    chart = compute_chart(eph, record, settings)
    match copy:
        case "calculation key":
            chart = replace(chart, calculation_key="0" * 64)
        case "angles' time":
            later = replace(chart.time, jd_ut1=chart.time.jd_ut1 + 1e-9)
            chart = replace(chart, angles=replace(chart.angles, time=later))
        case "pair's point":
            moved = replace(chart.pairs[0].a, longitude=chart.pairs[0].a.longitude + 1.0)
            chart = replace(chart, pairs=(replace(chart.pairs[0], a=moved), *chart.pairs[1:]))
        case _:
            sun = chart.positions.bodies[0]
            moved_sun = replace(sun, longitude=sun.longitude + 1.0)
            positions = replace(chart.positions, bodies=(moved_sun, *chart.positions.bodies[1:]))
            chart = replace(chart, positions=positions)
    with pytest.raises(ValueError, match="disagree"):
        chart_to_json(chart)


@pytest.mark.parametrize("part", ["angles", "time", "pair"])
def test_a_field_the_format_does_not_write_is_refused(eph: Ephemeris, part: str) -> None:
    # A field added to a result later must be added to the format too, or it would be dropped.
    record, settings = CASES["zoned-1987"]
    chart = compute_chart(eph, record, settings)
    match part:
        case "angles":
            chart, name = replace(chart, angles=_extended(chart.angles, ExtendedAngles)), "house"
        case "time":
            chart, name = replace(chart, time=_extended(chart.time, ExtendedTime)), "leap_seconds"
        case _:
            pair = _extended(chart.pairs[0], ExtendedPair)
            chart, name = replace(chart, pairs=(pair, *chart.pairs[1:])), "orb"
    with pytest.raises(TypeError, match=name):
        chart_to_json(chart)
