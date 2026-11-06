"""Birth records, chart settings and whole charts, each with a deterministic calculation key.

A `BirthRecord` is what a source says about one birth: a civil time, a place, and optionally
how precisely the time was recorded and the uncertainty the source states. It holds no names or
identifiers: subjects, sources and datasets are separate concepts.

`ChartSettings` names every choice the engine does not make itself: the time scale (the default
is undecided) and the aspect angles (a research choice). `compute_chart()` runs the engine on a
record: time, positions, angles, chart points and pair measurements.

The calculation key is the SHA-256 of `calculation_inputs()`, a canonical JSON of the whole
record, the settings, the ephemeris provenance, the convention strings, the tzdata version and
the engine version. Identical inputs give the same key in any process, and any change gives a
new one. Record metadata is included, so a cache keyed on it can miss but never returns a result
computed from other inputs. Equal values written differently (90 or 90.0, -0.0 or 0.0, any UTC
tzinfo) encode the same; the same instant written as another kind of civil time does not.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import importlib.metadata
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, assert_never

from astro_research.angles import ANGLE_CONVENTIONS
from astro_research.astronomical_time import TimeScale
from astro_research.civil_time import (
    TZDATA_VERSION,
    CivilTime,
    LocalMeanTime,
    OffsetLocalTime,
    UtcTime,
    ZonedLocalTime,
    resolve,
)
from astro_research.measurements import (
    MEASUREMENT_CONVENTIONS,
    ChartPoint,
    PairMeasurement,
    chart_points,
    measure,
    validated_aspect_angles,
)
from astro_research.positions import ALL_POINTS, POSITION_CONVENTIONS, Body, LunarPoint

if TYPE_CHECKING:  # type-only imports: nothing here needs the adapter at runtime
    from _typeshed import DataclassInstance

    from astro_research.angles import Angles
    from astro_research.astronomical_time import AstronomicalTime
    from astro_research.ephemeris import Ephemeris, EphemerisProvenance
    from astro_research.geo import GeoPosition
    from astro_research.positions import Point, Positions

KEY_SCHEMA = "chart-key-v1"
ENGINE_VERSION = importlib.metadata.version("astro-research")


class InvalidBirthRecord(ValueError):
    """The record contradicts itself."""


class InvalidChartSettings(ValueError):
    """The settings name an invalid aspect angle or point."""


class TimeResolution(enum.Enum):
    """How precisely the source recorded the clock reading."""

    HOUR = "hour"
    MINUTE = "minute"
    SECOND = "second"


@dataclass(frozen=True, kw_only=True)
class BirthRecord:
    time: CivilTime
    place: GeoPosition
    time_resolution: TimeResolution | None = None  # as the source recorded it
    stated_uncertainty: timedelta | None = None  # the +/- window the source states; never inferred

    def __post_init__(self) -> None:
        if self.stated_uncertainty is not None and self.stated_uncertainty <= timedelta(0):
            raise InvalidBirthRecord(
                f"a stated uncertainty must be positive, not {self.stated_uncertainty}"
            )
        if self.time_resolution is not None:
            reading = _clock_reading(self.time)
            if not _fits(reading, self.time_resolution):
                raise InvalidBirthRecord(
                    f"the reading {reading.isoformat()} is finer than its stated resolution "
                    f"({self.time_resolution.value})"
                )


@dataclass(frozen=True, kw_only=True)
class ChartSettings:
    time_scale: TimeScale
    aspect_angles: tuple[float, ...]  # stored as floats, in the order given
    points: tuple[Point, ...] = ALL_POINTS  # the Ascendant, MC and Vertex are always added

    def __post_init__(self) -> None:
        if not isinstance(self.time_scale, TimeScale):
            raise InvalidChartSettings(f"the time scale must be a TimeScale: {self.time_scale!r}")
        try:
            angles = validated_aspect_angles(self.aspect_angles)
        except ValueError as error:
            raise InvalidChartSettings(str(error)) from error
        points = tuple(self.points)
        if not all(isinstance(point, Body | LunarPoint) for point in points):
            raise InvalidChartSettings(
                f"points must be bodies or lunar points; the Ascendant, MC and Vertex are always "
                f"included: {points}"
            )
        if len(set(points)) != len(points):
            raise InvalidChartSettings(f"points must be distinct: {points}")
        object.__setattr__(self, "aspect_angles", angles)
        object.__setattr__(self, "points", points)


@dataclass(frozen=True, kw_only=True)
class Chart:
    record: BirthRecord
    settings: ChartSettings
    time: AstronomicalTime  # its instant keeps the record's input exactly as given
    positions: Positions
    angles: Angles
    points: tuple[ChartPoint, ...]  # settings.points in order, then Ascendant, MC, Vertex
    pairs: tuple[PairMeasurement, ...]  # every unordered pair of points, in order
    measurement_conventions: str
    calculation_key: str


def compute_chart(eph: Ephemeris, record: BirthRecord, settings: ChartSettings) -> Chart:
    """Run the engine on `record` under `settings`; civil-time and angle refusals propagate."""
    time = eph.time(resolve(record.time, record.place), settings.time_scale)
    positions = eph.positions(time, settings.points)
    angles = eph.angles(time, record.place)
    points = chart_points(positions, angles)
    return Chart(
        record=record,
        settings=settings,
        time=time,
        positions=positions,
        angles=angles,
        points=points,
        pairs=measure(points, settings.aspect_angles),
        measurement_conventions=MEASUREMENT_CONVENTIONS,
        calculation_key=calculation_key(record, settings, eph.provenance),
    )


def calculation_key(
    record: BirthRecord, settings: ChartSettings, ephemeris: EphemerisProvenance
) -> str:
    """SHA-256 (hex) of `calculation_inputs()`; computable before any calculation."""
    return hashlib.sha256(calculation_inputs(record, settings, ephemeris).encode()).hexdigest()


def calculation_inputs(
    record: BirthRecord, settings: ChartSettings, ephemeris: EphemerisProvenance
) -> str:
    """Everything a chart is computed from, as JSON with sorted keys and no whitespace.

    Floats are written as Python's shortest repr, which reads back exactly.
    """
    inputs = {
        "schema": KEY_SCHEMA,
        "engine": {"package": "astro-research", "version": ENGINE_VERSION},
        "tzdata": TZDATA_VERSION,
        "conventions": {
            "positions": POSITION_CONVENTIONS,
            "angles": ANGLE_CONVENTIONS,
            "measurements": MEASUREMENT_CONVENTIONS,
        },
        "record": _covering(
            record,
            {
                "time": _encode_time(record.time),
                "place": _covering(
                    record.place,
                    {
                        "latitude": _number(record.place.latitude),
                        "longitude": _number(record.place.longitude),
                        "elevation_m": _number(record.place.elevation_m),
                    },
                ),
                "time_resolution": _enum_value(record.time_resolution),
                "stated_uncertainty_us": _microseconds(record.stated_uncertainty),
            },
        ),
        "settings": _covering(
            settings,
            {
                "time_scale": settings.time_scale.value,
                "aspect_angles": [_number(angle) for angle in settings.aspect_angles],
                "points": [point.value for point in settings.points],
            },
        ),
        # Plain strings, integers and tuples of them: every field, as it is.
        "ephemeris": dataclasses.asdict(ephemeris),
    }
    return json.dumps(inputs, sort_keys=True, separators=(",", ":"), allow_nan=False)


# --- Record checks --------------------------------------------------------------------------


def _clock_reading(time: CivilTime) -> datetime:
    match time:
        case UtcTime():
            return time.utc
        case ZonedLocalTime() | OffsetLocalTime() | LocalMeanTime():
            return time.local
        case _:
            assert_never(time)


def _fits(reading: datetime, resolution: TimeResolution) -> bool:
    match resolution:
        case TimeResolution.HOUR:
            return reading.minute == reading.second == reading.microsecond == 0
        case TimeResolution.MINUTE:
            return reading.second == reading.microsecond == 0
        case TimeResolution.SECOND:
            return reading.microsecond == 0
        case _:
            assert_never(resolution)


# --- Canonical encoding ---------------------------------------------------------------------


def _encode_time(time: CivilTime) -> dict[str, object]:
    match time:
        case UtcTime():
            # isoformat() writes any zero-offset tzinfo as +00:00, so they all encode the same.
            return _covering(time, {"kind": "utc", "utc": time.utc.isoformat()})
        case ZonedLocalTime():
            return _covering(
                time,
                {
                    "kind": "zoned_local_time",
                    "local": time.local.isoformat(),
                    "zone": time.zone,
                    "stated_utc_offset_us": _microseconds(time.stated_utc_offset),
                    "accept_pre_1970_tzdb": time.accept_pre_1970_tzdb,
                },
            )
        case OffsetLocalTime():
            return _covering(
                time,
                {
                    "kind": "offset_local_time",
                    "local": time.local.isoformat(),
                    "utc_offset_us": _microseconds(time.utc_offset),
                },
            )
        case LocalMeanTime():
            return _covering(time, {"kind": "local_mean_time", "local": time.local.isoformat()})
        case _:
            assert_never(time)


def _covering(obj: DataclassInstance, encoded: dict[str, object]) -> dict[str, object]:
    """`encoded`, after checking it names every field of `obj`: a new field must join the key."""
    names = {name.removesuffix("_us") for name in encoded}
    missing = [field.name for field in dataclasses.fields(obj) if field.name not in names]
    if missing:
        raise TypeError(f"the calculation key leaves out {type(obj).__name__} fields {missing}")
    return encoded


def _number(value: float) -> float:
    return float(value) + 0.0  # adding 0.0 turns -0.0 into 0.0, which compares equal to it


def _microseconds(duration: timedelta | None) -> int | None:
    return None if duration is None else duration // timedelta(microseconds=1)


def _enum_value(member: enum.Enum | None) -> object:
    return None if member is None else member.value
