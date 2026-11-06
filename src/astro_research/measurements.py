"""Raw measurements between chart points: separations, deviations from exact aspects, parallels.

Only continuous numbers: no orbs, no aspect flags, no applying/separating labels and no scores.
Those are rules a researcher applies on top. This module never imports `swisseph`, so it works
on any stored chart.

Conventions, recorded in every chart as `MEASUREMENT_CONVENTIONS`:
- separations use ecliptic longitude, as zodiacal aspects do. `great_circle_separation` is the
  true angle on the sky, latitude included;
- the signed longitude difference is b - a, in (-180, 180]; `separation` is its absolute value;
- an aspect deviation is separation - angle, positive when wider than exact. Its rate, like the
  separation's, is in degrees per day and negative while closing. At an exact conjunction or
  opposition the separation has a corner, and the rate takes the sign the formula gives there;
- parallels: the declination difference (b - a) is zero at an exact parallel, the declination
  sum at an exact contra-parallel;
- chart points are the bodies and lunar points as given, then the Ascendant, MC and Vertex. The
  Descendant and IC are left out: a contact with one is a contact with the Ascendant or MC at
  the opposite angle, so they would add correlated measurements;
- an angle's declination is asin(sin(longitude) sin(true obliquity)); its rate follows by the
  chain rule with the obliquity held fixed (its change, ~0.5 arcsec a year, is neglected).
"""

from __future__ import annotations

import enum
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # type-only imports: measurements need values, not the modules that make them
    from astro_research.angles import Angles
    from astro_research.positions import Body, LunarPoint, Positions

MEASUREMENT_CONVENTIONS = (
    "measurements-v1: separations in ecliptic longitude, signed difference b - a in (-180, 180]; "
    "deviation = separation - aspect angle; rates per day, negative while closing; parallels by "
    "declination difference and sum; points are the bodies and lunar points, then Ascendant, MC "
    "and Vertex (no Descendant or IC); angle declinations from the true obliquity"
)

MAJOR = (0.0, 60.0, 90.0, 120.0, 180.0)
OCTILE = (45.0, 90.0, 135.0, 180.0)


class ChartAngle(enum.Enum):
    ASCENDANT = "ascendant"
    MIDHEAVEN = "midheaven"
    VERTEX = "vertex"


@dataclass(frozen=True, kw_only=True)
class ChartPoint:
    point: Body | LunarPoint | ChartAngle
    longitude: float  # degrees, true ecliptic and equinox of date
    latitude: float
    declination: float
    longitude_speed: float  # degrees per day
    declination_speed: float


@dataclass(frozen=True, kw_only=True)
class AspectDeviation:
    angle: float  # the aspect angle, e.g. 90.0
    deviation: float  # separation - angle; positive = wider than exact
    deviation_rate: float  # degrees per day; negative = closing


@dataclass(frozen=True, kw_only=True)
class PairMeasurement:
    a: ChartPoint
    b: ChartPoint
    longitude_difference: float  # b - a, in (-180, 180]
    separation: float  # |longitude_difference|
    separation_rate: float  # degrees per day
    great_circle_separation: float  # angle on the sky, latitude included
    deviations: tuple[AspectDeviation, ...]  # one per requested aspect angle, in order
    declination_difference: float  # b - a; zero at an exact parallel
    declination_sum: float  # b + a; zero at an exact contra-parallel
    declination_difference_rate: float  # degrees per day
    declination_sum_rate: float


def chart_points(positions: Positions, angles: Angles) -> tuple[ChartPoint, ...]:
    """The positions' bodies and lunar points in order, then the Ascendant, MC and Vertex."""
    from_positions = tuple(
        ChartPoint(
            point=p.body,
            longitude=p.longitude,
            latitude=p.latitude,
            declination=p.declination,
            longitude_speed=p.longitude_speed,
            declination_speed=p.declination_speed,
        )
        for p in positions.bodies
    )
    sin_eps = math.sin(math.radians(angles.true_obliquity))

    def on_ecliptic(which: ChartAngle, longitude: float, speed: float) -> ChartPoint:
        lam = math.radians(longitude)
        dec = math.asin(math.sin(lam) * sin_eps)
        return ChartPoint(
            point=which,
            longitude=longitude,
            latitude=0.0,
            declination=math.degrees(dec),
            longitude_speed=speed,
            declination_speed=math.cos(lam) * sin_eps / math.cos(dec) * speed,
        )

    return (
        *from_positions,
        on_ecliptic(ChartAngle.ASCENDANT, angles.ascendant, angles.ascendant_speed),
        on_ecliptic(ChartAngle.MIDHEAVEN, angles.midheaven, angles.midheaven_speed),
        on_ecliptic(ChartAngle.VERTEX, angles.vertex, angles.vertex_speed),
    )


def measure(
    points: Sequence[ChartPoint], aspect_angles: Sequence[float]
) -> tuple[PairMeasurement, ...]:
    """Every unordered pair of `points`, in order, against every angle in `aspect_angles`."""
    angles = validated_aspect_angles(aspect_angles)
    return tuple(_measure_pair(a, b, angles) for i, a in enumerate(points) for b in points[i + 1 :])


def validated_aspect_angles(aspect_angles: Sequence[float]) -> tuple[float, ...]:
    """The angles as floats; ValueError unless each lies within 0-180 degrees and none repeats."""
    angles = tuple(float(angle) for angle in aspect_angles)
    if any(not 0.0 <= angle <= 180.0 for angle in angles):
        raise ValueError(f"aspect angles must lie within 0-180 degrees: {angles}")
    if len(set(angles)) != len(angles):
        raise ValueError(f"aspect angles must be distinct: {angles}")
    return angles


def _measure_pair(a: ChartPoint, b: ChartPoint, angles: tuple[float, ...]) -> PairMeasurement:
    difference = (b.longitude - a.longitude) % 360.0
    if difference > 180.0:
        difference -= 360.0
    relative_speed = b.longitude_speed - a.longitude_speed
    separation = abs(difference)
    separation_rate = relative_speed if difference >= 0 else -relative_speed
    return PairMeasurement(
        a=a,
        b=b,
        longitude_difference=difference,
        separation=separation,
        separation_rate=separation_rate,
        great_circle_separation=_great_circle(a, b),
        deviations=tuple(
            AspectDeviation(
                angle=angle, deviation=separation - angle, deviation_rate=separation_rate
            )
            for angle in angles
        ),
        declination_difference=b.declination - a.declination,
        declination_sum=b.declination + a.declination,
        declination_difference_rate=b.declination_speed - a.declination_speed,
        declination_sum_rate=b.declination_speed + a.declination_speed,
    )


def _great_circle(a: ChartPoint, b: ChartPoint) -> float:
    """Angle between the two ecliptic directions; atan2 stays accurate at small angles."""

    def unit(p: ChartPoint) -> tuple[float, float, float]:
        lon, lat = math.radians(p.longitude), math.radians(p.latitude)
        return (math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat))

    (ax, ay, az), (bx, by, bz) = unit(a), unit(b)
    cross = math.hypot(ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)
    return math.degrees(math.atan2(cross, ax * bx + ay * by + az * bz))
