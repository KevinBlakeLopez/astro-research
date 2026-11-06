"""Birth-time sensitivity: every measurement recomputed at offsets from the recorded time.

`sweep()` computes the record's chart, then a whole chart at each offset, and compares each one
with the record's chart through `compare()`. Values are therefore exact, not estimated from the
speeds; measured at +10 minutes, such estimates are off by up to 197 arcsec on the Ascendant and
by 4.7 degrees for a pair passing an exact opposition. A chart costs about half a millisecond.

Series hold continuous numbers. Nothing marks a measurement as "sensitive": `range` and
`by_range()` show which barely change and which move a lot.

Conventions:
- offsets shift the record's resolved UTC instant by clock time, so a sweep never lands on a
  nonexistent or ambiguous local time. Across a leap second, elapsed time is a second longer,
  which each comparison's `tt_seconds` shows under `TimeScale.UT1_FROM_DELTA_T`;
- each offset's chart is the chart of a UTC instant, with its own calculation key. The record
  and its stated uncertainty stay on the base chart; the sweep never reads the uncertainty;
- offsets are sorted and zero is added; repeats are refused. There is no default set;
- changes are B - A as `compare()` gives them, wrapped into (-180, 180]. A point that moves more
  than 180 degrees from its base value within the sweep would be misread; that takes the
  Ascendant hours, not minutes;
- `base_rate` is the rate at the record's time, in degrees per day: a local slope, which has
  the wrong sign for a pair passing an exact conjunction or opposition inside the sweep;
- series involving the Ascendant, MC or Vertex are `rotation_driven`. Local sidereal time (the
  ARMC, recorded in each comparison) fixes all three angles, so for statistics they are one
  factor, not three;
- `crosses_sidereal_time_switch` flags a sweep spanning Swiss Ephemeris's switch of sidereal-time
  model (1850-01-01 and 2050-01-01 UT1), where the angles jump by 97 mas and 1.9 arcsec. Steps
  at the compressed Moon file's segment boundaries (up to 0.9 arcsec on the osculating apogee)
  cannot be predicted from dates and are not flagged.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from astro_research.angles import SIDEREAL_TIME_SWITCHES_JD_UT1
from astro_research.chart import BirthRecord, Chart, ChartSettings, compute_chart
from astro_research.civil_time import UtcTime
from astro_research.comparison import ChartComparison, compare
from astro_research.measurements import ChartAngle

if TYPE_CHECKING:  # type-only imports: a sweep works through Ephemeris, not the binding
    from astro_research.ephemeris import Ephemeris
    from astro_research.positions import Body, LunarPoint


class InvalidOffsets(ValueError):
    """The offsets repeat."""


class Quantity(enum.Enum):
    LONGITUDE = "longitude"  # of a point
    DECLINATION = "declination"  # of a point
    SIGNED_DIFFERENCE = "signed_difference"  # of a pair: b - a, in (-180, 180]
    SEPARATION = "separation"
    DEVIATION = "deviation"  # separation - aspect angle
    DECLINATION_DIFFERENCE = "declination_difference"  # zero at an exact parallel
    DECLINATION_SUM = "declination_sum"  # zero at an exact contra-parallel


@dataclass(frozen=True, kw_only=True)
class MeasurementSeries:
    quantity: Quantity
    points: tuple[Body | LunarPoint | ChartAngle, ...]  # one point, or a pair in chart order
    aspect_angle: float | None  # deviations only
    rotation_driven: bool  # involves the Ascendant, MC or Vertex
    values: tuple[float, ...]  # one per offset, as each chart has it
    changes: tuple[float, ...]  # one per offset, from compare(); zero at offset 0
    range: float  # max(changes) - min(changes)
    base_rate: float  # degrees per day at the record's time; a local slope only


@dataclass(frozen=True, kw_only=True)
class SweepRow:
    """One series at one offset: a flat row for tables and plots."""

    offset_s: float
    quantity: Quantity
    point_a: Body | LunarPoint | ChartAngle
    point_b: Body | LunarPoint | ChartAngle | None  # None for a single point's series
    aspect_angle: float | None
    rotation_driven: bool
    value: float
    change: float


@dataclass(frozen=True, kw_only=True)
class SensitivitySweep:
    base: Chart  # the record's own chart
    offsets: tuple[timedelta, ...]  # sorted, zero included
    comparisons: tuple[ChartComparison, ...]  # the base against each offset's chart, in order
    series: tuple[MeasurementSeries, ...]  # points first, then pairs, in chart order
    crosses_sidereal_time_switch: bool

    def by_range(self) -> tuple[MeasurementSeries, ...]:
        """The series from the widest range of change to the narrowest; ties keep their order."""
        return tuple(sorted(self.series, key=lambda series: series.range, reverse=True))

    def rows(self) -> tuple[SweepRow, ...]:
        """One row per series per offset, series by series."""
        return tuple(
            SweepRow(
                offset_s=offset.total_seconds(),
                quantity=series.quantity,
                point_a=series.points[0],
                point_b=series.points[1] if len(series.points) == 2 else None,
                aspect_angle=series.aspect_angle,
                rotation_driven=series.rotation_driven,
                value=value,
                change=change,
            )
            for series in self.series
            for offset, value, change in zip(
                self.offsets, series.values, series.changes, strict=True
            )
        )


def sweep(
    eph: Ephemeris, record: BirthRecord, settings: ChartSettings, offsets: Iterable[timedelta]
) -> SensitivitySweep:
    """The record's chart against a whole chart at each offset; civil-time refusals propagate."""
    given = tuple(offsets)
    if len(set(given)) != len(given):
        raise InvalidOffsets(f"offsets must not repeat: {[str(offset) for offset in given]}")
    ordered = tuple(sorted({*given, timedelta(0)}))
    base = compute_chart(eph, record, settings)
    utc = base.time.instant.utc
    charts = tuple(
        base
        if offset == timedelta(0)
        else compute_chart(
            eph, BirthRecord(time=UtcTime(utc=utc + offset), place=record.place), settings
        )
        for offset in ordered
    )
    comparisons = tuple(compare(base, chart) for chart in charts)
    return SensitivitySweep(
        base=base,
        offsets=ordered,
        comparisons=comparisons,
        series=_series(base, comparisons),
        crosses_sidereal_time_switch=len({_sidereal_model(c.time.jd_ut1) for c in charts}) > 1,
    )


def _series(base: Chart, comparisons: Sequence[ChartComparison]) -> tuple[MeasurementSeries, ...]:
    out: list[MeasurementSeries] = []

    def add(
        quantity: Quantity,
        points: tuple[Body | LunarPoint | ChartAngle, ...],
        values: Iterable[float],
        changes: Iterable[float],
        base_rate: float,
        aspect_angle: float | None = None,
    ) -> None:
        change_tuple = tuple(changes)
        out.append(
            MeasurementSeries(
                quantity=quantity,
                points=points,
                aspect_angle=aspect_angle,
                rotation_driven=any(isinstance(point, ChartAngle) for point in points),
                values=tuple(values),
                changes=change_tuple,
                range=max(change_tuple) - min(change_tuple),
                base_rate=base_rate,
            )
        )

    for i, point in enumerate(base.points):
        point_moves = [c.points[i] for c in comparisons]
        which = (point.point,)
        add(
            Quantity.LONGITUDE,
            which,
            (m.b.longitude for m in point_moves),
            (m.longitude for m in point_moves),
            point.longitude_speed,
        )
        add(
            Quantity.DECLINATION,
            which,
            (m.b.declination for m in point_moves),
            (m.declination for m in point_moves),
            point.declination_speed,
        )
    for j, pair in enumerate(base.pairs):
        pair_moves = [c.pairs[j] for c in comparisons]
        both = (pair.a.point, pair.b.point)
        add(
            Quantity.SIGNED_DIFFERENCE,
            both,
            (m.b.longitude_difference for m in pair_moves),
            (m.longitude_difference for m in pair_moves),
            pair.b.longitude_speed - pair.a.longitude_speed,
        )
        add(
            Quantity.SEPARATION,
            both,
            (m.b.separation for m in pair_moves),
            (m.separation for m in pair_moves),
            pair.separation_rate,
        )
        for k, deviation in enumerate(pair.deviations):
            add(
                Quantity.DEVIATION,
                both,
                (m.deviations[k].b.deviation for m in pair_moves),
                (m.deviations[k].deviation for m in pair_moves),
                deviation.deviation_rate,
                aspect_angle=deviation.angle,
            )
        add(
            Quantity.DECLINATION_DIFFERENCE,
            both,
            (m.b.declination_difference for m in pair_moves),
            (m.declination_difference for m in pair_moves),
            pair.declination_difference_rate,
        )
        add(
            Quantity.DECLINATION_SUM,
            both,
            (m.b.declination_sum for m in pair_moves),
            (m.declination_sum for m in pair_moves),
            pair.declination_sum_rate,
        )
    return tuple(out)


def _sidereal_model(jd_ut1: float) -> str:
    early, late = SIDEREAL_TIME_SWITCHES_JD_UT1
    if jd_ut1 <= early:
        return "long-term, before 1850"
    if jd_ut1 >= late:
        return "long-term, from 2050"
    return "IERS 2010"
