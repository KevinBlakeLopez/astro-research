"""Two-chart comparison: chart B minus chart A, for every number the charts hold.

Both charts travel with the comparison, so absolute values and changes are read together.
Changes are continuous numbers: nothing says an aspect or contact was gained or lost, because
that would need an orb. This module never imports `swisseph`.

Conventions:
- every change is B - A. Changes in longitude, in ARMC and in a pair's signed difference are
  wrapped into (-180, 180], so a change of more than 180 degrees reads as the smaller change the
  other way; charts minutes apart never come close;
- `utc_seconds` is civil time and does not count leap seconds. `tt_seconds` is elapsed time from
  the two TT Julian dates, whose float resolution is about 40 microseconds today;
- under `TimeScale.CLOCK_AS_UT1` a pair that straddles a leap second loses that second in TT
  and in the angles too (observed across 2016-12-31 23:59:60: 60 s instead of 61, and the
  Ascendant's change 10.6 arcsec smaller). Otherwise the convention barely affects a change;
- a deviation from an aspect angle changes by exactly the change in separation, whatever the
  angle; both charts' deviations are kept so exactness can be read side by side;
- the Ascendant, MC and Vertex all turn with the Earth, so the contacts in `involving_angles()`
  move together and are not independent measurements;
- charts are compared only under the same settings, ephemeris data and conventions. Their places
  may differ.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from astro_research.measurements import ChartAngle

if TYPE_CHECKING:  # type-only imports: a comparison needs values, not the modules that make them
    from astro_research.chart import Chart
    from astro_research.measurements import AspectDeviation, ChartPoint, PairMeasurement
    from astro_research.positions import Body, LunarPoint


class ChartsNotComparable(ValueError):
    """The charts were computed under different settings, ephemeris data or conventions."""


@dataclass(frozen=True, kw_only=True)
class PointChange:
    """One chart point in both charts; every number is B's minus A's."""

    point: Body | LunarPoint | ChartAngle
    a: ChartPoint
    b: ChartPoint
    longitude: float  # wrapped into (-180, 180]
    latitude: float
    declination: float
    longitude_speed: float  # degrees per day
    declination_speed: float


@dataclass(frozen=True, kw_only=True)
class DeviationChange:
    angle: float
    a: AspectDeviation
    b: AspectDeviation
    deviation: float  # B - A; the pair's change in separation, for every angle


@dataclass(frozen=True, kw_only=True)
class PairChange:
    """One pair's measurements in both charts; every number is B's minus A's."""

    a: PairMeasurement
    b: PairMeasurement
    longitude_difference: float  # wrapped into (-180, 180]
    separation: float
    great_circle_separation: float
    deviations: tuple[DeviationChange, ...]  # one per aspect angle, in the settings' order
    declination_difference: float  # change in the parallel measure
    declination_sum: float  # change in the contra-parallel measure


@dataclass(frozen=True, kw_only=True)
class ChartComparison:
    a: Chart
    b: Chart
    utc_seconds: float  # civil; leap seconds not counted
    tt_seconds: float  # elapsed
    armc: float  # wrapped into (-180, 180]
    points: tuple[PointChange, ...]  # in the charts' order
    pairs: tuple[PairChange, ...]

    def involving_angles(self) -> tuple[PairChange, ...]:
        """The pairs with the Ascendant, MC or Vertex, which turn together with the Earth."""
        return tuple(
            change
            for change in self.pairs
            if isinstance(change.a.a.point, ChartAngle) or isinstance(change.a.b.point, ChartAngle)
        )


def compare(a: Chart, b: Chart) -> ChartComparison:
    """B minus A for every number; refused unless both were computed the same way."""
    _require_comparable(a, b)
    return ChartComparison(
        a=a,
        b=b,
        utc_seconds=(b.time.instant.utc - a.time.instant.utc).total_seconds(),
        tt_seconds=(b.time.jd_tt - a.time.jd_tt) * 86400,
        armc=_wrap(b.angles.armc - a.angles.armc),
        points=tuple(_point_change(pa, pb) for pa, pb in zip(a.points, b.points, strict=True)),
        pairs=tuple(_pair_change(ma, mb) for ma, mb in zip(a.pairs, b.pairs, strict=True)),
    )


def _require_comparable(a: Chart, b: Chart) -> None:
    if a.settings != b.settings:
        raise ChartsNotComparable(f"the charts' settings differ: {a.settings} and {b.settings}")
    sources = {a.positions.ephemeris, a.angles.ephemeris, b.positions.ephemeris, b.angles.ephemeris}
    if len(sources) != 1:
        raise ChartsNotComparable(f"the charts come from different ephemeris data: {sources}")
    conventions_a = (a.positions.conventions, a.angles.conventions, a.measurement_conventions)
    conventions_b = (b.positions.conventions, b.angles.conventions, b.measurement_conventions)
    if conventions_a != conventions_b:
        raise ChartsNotComparable(
            f"the charts follow different conventions: {conventions_a} and {conventions_b}"
        )


def _point_change(a: ChartPoint, b: ChartPoint) -> PointChange:
    if a.point is not b.point:
        raise ChartsNotComparable(f"the charts' points do not line up: {a.point} and {b.point}")
    return PointChange(
        point=a.point,
        a=a,
        b=b,
        longitude=_wrap(b.longitude - a.longitude),
        latitude=b.latitude - a.latitude,
        declination=b.declination - a.declination,
        longitude_speed=b.longitude_speed - a.longitude_speed,
        declination_speed=b.declination_speed - a.declination_speed,
    )


def _pair_change(a: PairMeasurement, b: PairMeasurement) -> PairChange:
    if (a.a.point, a.b.point) != (b.a.point, b.b.point):
        raise ChartsNotComparable(
            f"the charts' pairs do not line up: {a.a.point}-{a.b.point} and {b.a.point}-{b.b.point}"
        )
    return PairChange(
        a=a,
        b=b,
        longitude_difference=_wrap(b.longitude_difference - a.longitude_difference),
        separation=b.separation - a.separation,
        great_circle_separation=b.great_circle_separation - a.great_circle_separation,
        deviations=tuple(
            DeviationChange(angle=da.angle, a=da, b=db, deviation=db.deviation - da.deviation)
            for da, db in zip(a.deviations, b.deviations, strict=True)
        ),
        declination_difference=b.declination_difference - a.declination_difference,
        declination_sum=b.declination_sum - a.declination_sum,
    )


def _wrap(degrees: float) -> float:
    """Into (-180, 180]. Every input here lies within (-360, 360), so one turn is enough."""
    if degrees > 180.0:
        return degrees - 360.0
    if degrees <= -180.0:
        return degrees + 360.0
    return degrees
