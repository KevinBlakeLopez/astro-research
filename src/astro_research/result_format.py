"""Charts as JSON documents, format `chart-result-v1`, read back exactly.

A document writes each value once, in this order:

- `format` and `calculation_key`;
- `inputs`: `calculation_inputs()` verbatim, holding the record, settings, ephemeris provenance,
  convention strings, tzdata and engine versions;
- `time`: the resolved UTC reading and the Julian dates, with where UT1 came from;
- `bodies`, `angles` and `points`, as the engine computed them (a body's point repeats five of the
  body's values, and reading checks that they agree);
- `pairs`, which name their two points instead of repeating them.

Floats are written as Python's shortest repr, which reads back bit for bit, including -0.0.
Durations are whole microseconds and instants ISO 8601, as in the calculation key.

Reading is strict and repairs nothing. It refuses an unknown format, a missing or extra field, a
value of the wrong type, a structure the engine never produces, and inputs whose SHA-256 isn't
the calculation key. The inputs are hashed as stored, not rebuilt with today's code, so a chart
from another engine version reads as it was written. Result values aren't covered by the key:
checking them means computing the chart again.
"""

import enum
import hashlib
import json
from collections.abc import Iterable
from dataclasses import fields
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from astro_research.angles import Angles
from astro_research.astronomical_time import AstronomicalTime, TimeScale, Ut1Basis
from astro_research.chart import (
    KEY_SCHEMA,
    BirthRecord,
    Chart,
    ChartSettings,
    TimeResolution,
    calculation_inputs,
)
from astro_research.civil_time import (
    CivilTime,
    LocalMeanTime,
    OffsetLocalTime,
    OffsetSource,
    ResolvedInstant,
    UtcTime,
    ZonedLocalTime,
)
from astro_research.ephemeris import EphemerisProvenance
from astro_research.geo import GeoPosition
from astro_research.measurements import AspectDeviation, ChartAngle, ChartPoint, PairMeasurement
from astro_research.positions import Body, BodyPosition, LunarPoint, Point, Positions

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

CHART_FORMAT = "chart-result-v1"

# The numbers each part writes, by field name, in the order written. Writing and reading both
# use these, so the format is defined once.
_TIME_FLOATS = ("jd_ut1", "jd_tt", "delta_t_s", "ut1_minus_utc_s")
_BODY_FLOATS = (
    "longitude",
    "latitude",
    "distance_au",
    "longitude_speed",
    "latitude_speed",
    "distance_speed",
    "right_ascension",
    "declination",
    "right_ascension_speed",
    "declination_speed",
)
_ANGLE_FLOATS = (
    "armc",
    "armc_speed",
    "ascendant",
    "ascendant_speed",
    "midheaven",
    "midheaven_speed",
    "descendant",
    "imum_coeli",
    "vertex",
    "vertex_speed",
    "true_obliquity",
)
_POINT_FLOATS = ("longitude", "latitude", "declination", "longitude_speed", "declination_speed")
_PAIR_FLOATS = ("longitude_difference", "separation", "separation_rate", "great_circle_separation")
_PAIR_DECLINATION_FLOATS = (
    "declination_difference",
    "declination_sum",
    "declination_difference_rate",
    "declination_sum_rate",
)
_DEVIATION_FLOATS = ("angle", "deviation", "deviation_rate")
_SECTIONS = ("format", "calculation_key", "inputs", "time", "bodies", "angles", "points", "pairs")
_POINTS_BY_VALUE: dict[str, Point | ChartAngle] = {
    member.value: member for member in (*Body, *LunarPoint, *ChartAngle)
}


class InvalidChartDocument(ValueError):
    """Text this format can't read as a chart; refused, never repaired."""


# --- Writing --------------------------------------------------------------------------------


def chart_to_json(chart: Chart) -> str:
    """The chart as compact JSON. Raises TypeError if a part has a field the format doesn't
    write, and ValueError if two copies of one value in the chart disagree."""
    provenance = chart.positions.ephemeris
    inputs = calculation_inputs(chart.record, chart.settings, provenance)
    document = {
        "format": CHART_FORMAT,
        "calculation_key": chart.calculation_key,
        "inputs": json.loads(inputs),
        "time": _write_time(chart.time),
        "bodies": [_write_body(body) for body in chart.positions.bodies],
        "angles": _write_angles(chart.angles),
        "points": [_write_point(point) for point in chart.points],
        "pairs": [_write_pair(pair) for pair in chart.pairs],
    }
    _check_written_once(chart, inputs)
    return json.dumps(document, separators=(",", ":"), allow_nan=False)


def _write_time(time: AstronomicalTime) -> dict[str, object]:
    instant = time.instant
    written: dict[str, object] = {
        "utc": instant.utc.isoformat(),
        "utc_offset_us": _microseconds(instant.utc_offset),
        "offset_source": instant.offset_source.value,
        "zone_abbreviation": instant.zone_abbreviation,
        "tzdata_version": instant.tzdata_version,
        "time_scale": time.time_scale.value,
        **{name: getattr(time, name) for name in _TIME_FLOATS},
        "ut1_basis": time.ut1_basis.value,
    }
    _covering(instant, written, elsewhere=("input",))  # the record's civil time
    _covering(time, written, elsewhere=("instant",))
    return written


def _write_body(body: BodyPosition) -> dict[str, object]:
    written = {"body": body.body.value, **{name: getattr(body, name) for name in _BODY_FLOATS}}
    return _covering(body, written)


def _write_angles(angles: Angles) -> dict[str, object]:
    written = {
        **{name: getattr(angles, name) for name in _ANGLE_FLOATS},
        "beyond_polar_circle": angles.beyond_polar_circle,
    }
    # The time, place, provenance and conventions are the chart's, written elsewhere.
    return _covering(angles, written, elsewhere=("time", "place", "ephemeris", "conventions"))


def _write_point(point: ChartPoint) -> dict[str, object]:
    written = {"point": point.point.value, **{name: getattr(point, name) for name in _POINT_FLOATS}}
    return _covering(point, written)


def _write_pair(pair: PairMeasurement) -> dict[str, object]:
    written = {
        "a": pair.a.point.value,
        "b": pair.b.point.value,
        **{name: getattr(pair, name) for name in _PAIR_FLOATS},
        "deviations": [_write_deviation(deviation) for deviation in pair.deviations],
        **{name: getattr(pair, name) for name in _PAIR_DECLINATION_FLOATS},
    }
    return _covering(pair, written)


def _write_deviation(deviation: AspectDeviation) -> dict[str, object]:
    return _covering(deviation, {name: getattr(deviation, name) for name in _DEVIATION_FLOATS})


def _check_written_once(chart: Chart, inputs: str) -> None:
    """Values the document holds once must agree wherever the chart holds them."""
    copies = {
        "calculation key": hashlib.sha256(inputs.encode()).hexdigest() == chart.calculation_key,
        "time": chart.positions.time == chart.time and chart.angles.time == chart.time,
        "time scale": chart.time.time_scale is chart.settings.time_scale,
        "civil time": chart.time.instant.input == chart.record.time,
        "place": chart.angles.place == chart.record.place,
        "provenance": chart.angles.ephemeris == chart.positions.ephemeris,
        "conventions": [
            chart.positions.conventions,
            chart.angles.conventions,
            chart.measurement_conventions,
        ]
        == list(json.loads(inputs)["conventions"][part] for part in _CONVENTION_PARTS),
        "pairs' points": all(
            _by_name(chart.points).get(point.point) == point
            for pair in chart.pairs
            for point in (pair.a, pair.b)
        ),
        "bodies' points": all(
            _by_name(chart.points).get(body.body) == _point_of(body)
            for body in chart.positions.bodies
        ),
    }
    if disagreeing := [name for name, agree in copies.items() if not agree]:
        raise ValueError(f"the chart's copies of its {', '.join(disagreeing)} disagree")


_CONVENTION_PARTS = ("positions", "angles", "measurements")


def _covering(
    obj: "DataclassInstance", written: dict[str, object], elsewhere: Iterable[str] = ()
) -> dict[str, object]:
    """`written`, after checking it holds every field of `obj` not written `elsewhere`."""
    names = {name.removesuffix("_us") for name in written} | set(elsewhere)
    if missing := [field.name for field in fields(obj) if field.name not in names]:
        raise TypeError(f"the chart format leaves out {type(obj).__name__} fields {missing}")
    return written


def _point_of(body: BodyPosition) -> ChartPoint:
    """The chart point the engine makes from a body: five of its values, repeated."""
    return ChartPoint(point=body.body, **{name: getattr(body, name) for name in _POINT_FLOATS})


def _microseconds(duration: timedelta) -> int:
    return duration // timedelta(microseconds=1)


def _by_name(points: Iterable[ChartPoint]) -> dict[Point | ChartAngle, ChartPoint]:
    return {point.point: point for point in points}


# --- Reading --------------------------------------------------------------------------------


def chart_from_json(text: str) -> Chart:
    """The chart a document holds; InvalidChartDocument unless the engine could have written it."""
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise InvalidChartDocument(f"not JSON: {error}") from None
    doc = _object(document, _SECTIONS, "the document")
    if doc["format"] != CHART_FORMAT:
        raise InvalidChartDocument(f"format {doc['format']!r} is not {CHART_FORMAT!r}")
    key = _string(doc, "calculation_key")
    if not isinstance(doc["inputs"], dict):
        raise InvalidChartDocument("inputs: not an object")
    stored = json.dumps(doc["inputs"], sort_keys=True, separators=(",", ":"), allow_nan=False)
    if hashlib.sha256(stored.encode()).hexdigest() != key:
        raise InvalidChartDocument("the inputs' SHA-256 is not the calculation key")
    try:
        return _read_chart(doc, key)
    except InvalidChartDocument:
        raise
    except (ValueError, TypeError) as error:  # a record, place or setting the engine refuses
        raise InvalidChartDocument(str(error)) from error


def _read_chart(doc: dict[str, Any], key: str) -> Chart:
    inputs = _object(
        doc["inputs"],
        ("schema", "engine", "tzdata", "conventions", "record", "settings", "ephemeris"),
        "inputs",
    )
    if inputs["schema"] != KEY_SCHEMA:
        raise InvalidChartDocument(f"inputs: key schema {inputs['schema']!r} is not {KEY_SCHEMA!r}")
    _object(inputs["engine"], ("package", "version"), "inputs.engine")
    conventions = _object(inputs["conventions"], _CONVENTION_PARTS, "inputs.conventions")
    record = _read_record(inputs["record"])
    settings = _read_settings(inputs["settings"])
    provenance = _read_provenance(inputs["ephemeris"])

    time = _read_time(doc["time"], record.time)
    if time.time_scale is not settings.time_scale:
        raise InvalidChartDocument("time: its time scale is not the settings'")
    bodies = tuple(_read_body(raw) for raw in _array(doc["bodies"], "bodies"))
    if tuple(body.body for body in bodies) != settings.points:
        raise InvalidChartDocument("bodies: not the settings' points, in their order")
    positions = Positions(
        time=time,
        ephemeris=provenance,
        conventions=_string(conventions, "positions"),
        bodies=bodies,
    )
    angles = _read_angles(doc["angles"], time, record.place, provenance, conventions)
    points = tuple(_read_point(raw) for raw in _array(doc["points"], "points"))
    if tuple(point.point for point in points) != (*settings.points, *ChartAngle):
        raise InvalidChartDocument("points: not the bodies, then the three angles")
    if any(point != _point_of(body) for point, body in zip(points, bodies, strict=False)):
        raise InvalidChartDocument("points: a body's point doesn't repeat the body's values")
    pairs = tuple(
        _read_pair(raw, _by_name(points), settings.aspect_angles)
        for raw in _array(doc["pairs"], "pairs")
    )
    expected = [(a.point, b.point) for i, a in enumerate(points) for b in points[i + 1 :]]
    if [(pair.a.point, pair.b.point) for pair in pairs] != expected:
        raise InvalidChartDocument("pairs: not every pair of points once, in order")
    return Chart(
        record=record,
        settings=settings,
        time=time,
        positions=positions,
        angles=angles,
        points=points,
        pairs=pairs,
        measurement_conventions=_string(conventions, "measurements"),
        calculation_key=key,
    )


def _read_record(raw: object) -> BirthRecord:
    record = _object(raw, ("time", "place", "time_resolution", "stated_uncertainty_us"), "record")
    place = _object(record["place"], ("latitude", "longitude", "elevation_m"), "record.place")
    resolution = record["time_resolution"]
    uncertainty = record["stated_uncertainty_us"]
    return BirthRecord(
        time=_read_civil_time(record["time"]),
        place=GeoPosition(
            latitude=_float(place, "latitude"),
            longitude=_float(place, "longitude"),
            elevation_m=_float(place, "elevation_m"),
        ),
        time_resolution=None if resolution is None else _enum(TimeResolution, resolution),
        stated_uncertainty=None
        if uncertainty is None
        else _duration(record, "stated_uncertainty_us"),
    )


def _read_civil_time(raw: object) -> CivilTime:
    if not isinstance(raw, dict):
        raise InvalidChartDocument("record.time: not an object")
    match raw.get("kind"):
        case "utc":
            time = _object(raw, ("kind", "utc"), "record.time")
            return UtcTime(utc=_instant(time, "utc"))
        case "zoned_local_time":
            names = ("kind", "local", "zone", "stated_utc_offset_us", "accept_pre_1970_tzdb")
            time = _object(raw, names, "record.time")
            stated = time["stated_utc_offset_us"]
            return ZonedLocalTime(
                local=_instant(time, "local"),
                zone=_string(time, "zone"),
                stated_utc_offset=None
                if stated is None
                else _duration(time, "stated_utc_offset_us"),
                accept_pre_1970_tzdb=_bool(time, "accept_pre_1970_tzdb"),
            )
        case "offset_local_time":
            time = _object(raw, ("kind", "local", "utc_offset_us"), "record.time")
            return OffsetLocalTime(
                local=_instant(time, "local"), utc_offset=_duration(time, "utc_offset_us")
            )
        case "local_mean_time":
            time = _object(raw, ("kind", "local"), "record.time")
            return LocalMeanTime(local=_instant(time, "local"))
        case kind:
            raise InvalidChartDocument(f"record.time: unknown kind {kind!r}")


def _read_settings(raw: object) -> ChartSettings:
    settings = _object(raw, ("time_scale", "aspect_angles", "points"), "settings")
    angles = _array(settings["aspect_angles"], "settings.aspect_angles")
    points = _array(settings["points"], "settings.points")
    return ChartSettings(
        aspect_angles=tuple(_float({"angle": angle}, "angle") for angle in angles),
        time_scale=_enum(TimeScale, settings["time_scale"]),
        points=tuple(_body_or_lunar_point(value) for value in points),
    )


def _read_provenance(raw: object) -> EphemerisProvenance:
    names = tuple(field.name for field in fields(EphemerisProvenance))
    provenance = _object(raw, names, "ephemeris")
    files = _array(provenance["file_sha256"], "ephemeris.file_sha256")
    if not all(
        isinstance(file, list) and len(file) == 2 and all(isinstance(s, str) for s in file)
        for file in files
    ):
        raise InvalidChartDocument("ephemeris.file_sha256: not [name, SHA-256] pairs")
    if type(provenance["de_number"]) is not int:
        raise InvalidChartDocument("ephemeris.de_number: not an integer")
    return EphemerisProvenance(
        binding=_string(provenance, "binding"),
        swisseph_version=_string(provenance, "swisseph_version"),
        de_number=provenance["de_number"],
        source_commit=_string(provenance, "source_commit"),
        file_sha256=tuple((name, sha) for name, sha in files),
        ut1_data_sha256=_string(provenance, "ut1_data_sha256"),
    )


def _read_time(raw: object, civil_time: CivilTime) -> AstronomicalTime:
    names = (
        "utc",
        "utc_offset_us",
        "offset_source",
        "zone_abbreviation",
        "tzdata_version",
        "time_scale",
        *_TIME_FLOATS,
        "ut1_basis",
    )
    time = _object(raw, names, "time")
    instant = ResolvedInstant(
        input=civil_time,
        utc=_instant(time, "utc"),
        utc_offset=_duration(time, "utc_offset_us"),
        offset_source=_enum(OffsetSource, time["offset_source"]),
        zone_abbreviation=_optional_string(time, "zone_abbreviation"),
        tzdata_version=_optional_string(time, "tzdata_version"),
    )
    return AstronomicalTime(
        instant=instant,
        time_scale=_enum(TimeScale, time["time_scale"]),
        **{name: _float(time, name) for name in _TIME_FLOATS},
        ut1_basis=_enum(Ut1Basis, time["ut1_basis"]),
    )


def _read_body(raw: object) -> BodyPosition:
    body = _object(raw, ("body", *_BODY_FLOATS), "bodies")
    return BodyPosition(
        body=_body_or_lunar_point(body["body"]),
        **{name: _float(body, name) for name in _BODY_FLOATS},
    )


def _read_angles(
    raw: object,
    time: AstronomicalTime,
    place: GeoPosition,
    provenance: EphemerisProvenance,
    conventions: dict[str, Any],
) -> Angles:
    angles = _object(raw, (*_ANGLE_FLOATS, "beyond_polar_circle"), "angles")
    return Angles(
        time=time,
        place=place,
        ephemeris=provenance,
        conventions=_string(conventions, "angles"),
        **{name: _float(angles, name) for name in _ANGLE_FLOATS},
        beyond_polar_circle=_bool(angles, "beyond_polar_circle"),
    )


def _read_point(raw: object) -> ChartPoint:
    point = _object(raw, ("point", *_POINT_FLOATS), "points")
    if not isinstance(point["point"], str) or point["point"] not in _POINTS_BY_VALUE:
        raise InvalidChartDocument(f"points: unknown point {point['point']!r}")
    return ChartPoint(
        point=_POINTS_BY_VALUE[point["point"]],
        **{name: _float(point, name) for name in _POINT_FLOATS},
    )


def _read_pair(
    raw: object,
    points: dict[Point | ChartAngle, ChartPoint],
    aspect_angles: tuple[float, ...],
) -> PairMeasurement:
    names = ("a", "b", *_PAIR_FLOATS, "deviations", *_PAIR_DECLINATION_FLOATS)
    pair = _object(raw, names, "pairs")
    ends = []
    for end in ("a", "b"):
        name = pair[end]
        member = _POINTS_BY_VALUE.get(name) if isinstance(name, str) else None
        if member is None or member not in points:
            raise InvalidChartDocument(f"pairs: {name!r} is not one of the chart's points")
        ends.append(points[member])
    deviations = tuple(
        AspectDeviation(
            **{
                name: _float(_object(entry, _DEVIATION_FLOATS, "deviations"), name)
                for name in _DEVIATION_FLOATS
            }
        )
        for entry in _array(pair["deviations"], "deviations")
    )
    if tuple(deviation.angle for deviation in deviations) != aspect_angles:
        raise InvalidChartDocument("deviations: not the settings' aspect angles, in their order")
    return PairMeasurement(
        a=ends[0],
        b=ends[1],
        **{name: _float(pair, name) for name in _PAIR_FLOATS},
        deviations=deviations,
        **{name: _float(pair, name) for name in _PAIR_DECLINATION_FLOATS},
    )


# --- Strict values --------------------------------------------------------------------------


def _object(raw: object, names: tuple[str, ...], where: str) -> dict[str, Any]:
    """`raw` as an object holding exactly the fields `names`."""
    if not isinstance(raw, dict):
        raise InvalidChartDocument(f"{where}: not an object")
    if missing := [name for name in names if name not in raw]:
        raise InvalidChartDocument(f"{where}: missing {missing}")
    if extra := [name for name in raw if name not in names]:
        raise InvalidChartDocument(f"{where}: unexpected {extra}")
    return raw


def _array(raw: object, where: str) -> list[Any]:
    if not isinstance(raw, list):
        raise InvalidChartDocument(f"{where}: not an array")
    return raw


def _float(raw: dict[str, Any], name: str) -> float:
    # JSON writes every float with a point or an exponent, so an integer or a boolean here
    # was not written by this format.
    value = raw[name]
    if type(value) is not float:
        raise InvalidChartDocument(f"{name}: {value!r} is not a float")
    return value


def _bool(raw: dict[str, Any], name: str) -> bool:
    value = raw[name]
    if type(value) is not bool:
        raise InvalidChartDocument(f"{name}: {value!r} is not true or false")
    return value


def _string(raw: dict[str, Any], name: str) -> str:
    value = raw[name]
    if not isinstance(value, str):
        raise InvalidChartDocument(f"{name}: {value!r} is not a string")
    return value


def _optional_string(raw: dict[str, Any], name: str) -> str | None:
    return None if raw[name] is None else _string(raw, name)


def _duration(raw: dict[str, Any], name: str) -> timedelta:
    value = raw[name]
    if type(value) is not int:
        raise InvalidChartDocument(f"{name}: {value!r} is not whole microseconds")
    return timedelta(microseconds=value)


def _instant(raw: dict[str, Any], name: str) -> datetime:
    try:
        return datetime.fromisoformat(_string(raw, name))
    except ValueError:
        raise InvalidChartDocument(f"{name}: {raw[name]!r} is not an ISO 8601 time") from None


def _enum[E: enum.Enum](cls: type[E], value: object) -> E:
    try:
        return cls(value)
    except ValueError:
        raise InvalidChartDocument(f"{value!r} is not a {cls.__name__}") from None


def _body_or_lunar_point(value: object) -> Point:
    member = _POINTS_BY_VALUE.get(value) if isinstance(value, str) else None
    if not isinstance(member, Body | LunarPoint):
        raise InvalidChartDocument(f"{value!r} is not a body or lunar point")
    return member
