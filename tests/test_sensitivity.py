"""Birth-time sensitivity: every measurement recomputed at offsets from the recorded time.

Each offset gets a whole chart of its own, compared with the record's chart, so the series are
exact rather than estimated from the speeds (which miss pairs turning at an exact aspect).
Series are continuous numbers with no threshold for "sensitive"; anything involving the
Ascendant, MC or Vertex is marked as driven by the Earth's rotation, so the three angles are not
mistaken for independent evidence. The records are synthetic.
"""

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from astro_research.angles import SIDEREAL_TIME_SWITCHES_JD_UT1
from astro_research.astronomical_time import TimeScale
from astro_research.chart import BirthRecord, Chart, ChartSettings, compute_chart
from astro_research.civil_time import (
    AmbiguousLocalTime,
    NonexistentLocalTime,
    UtcTime,
    ZonedLocalTime,
    resolve,
)
from astro_research.comparison import ChartComparison, compare
from astro_research.ephemeris import Ephemeris
from astro_research.geo import GeoPosition
from astro_research.measurements import MAJOR, OCTILE, ChartAngle, ChartPoint, PairMeasurement
from astro_research.positions import ALL_POINTS, Body
from astro_research.sensitivity import (
    InvalidOffsets,
    MeasurementSeries,
    Quantity,
    SensitivitySweep,
    sweep,
)

LONDON = GeoPosition(latitude=51.5074, longitude=-0.1278)
BASE_UTC = datetime(1987, 6, 15, 3, 25, tzinfo=UTC)
RECORD = BirthRecord(time=UtcTime(utc=BASE_UTC), place=LONDON)
ASPECTS = tuple(sorted({*MAJOR, *OCTILE}))
SETTINGS = ChartSettings(time_scale=TimeScale.CLOCK_AS_UT1, aspect_angles=ASPECTS)
SPEC_EXAMPLE = tuple(timedelta(minutes=m) for m in (-10, -5, -1, 1, 5, 10))
POINT_QUANTITIES = (Quantity.LONGITUDE, Quantity.DECLINATION)
PAIR_QUANTITIES = (
    Quantity.SIGNED_DIFFERENCE,
    Quantity.SEPARATION,
    Quantity.DEVIATION,
    Quantity.DECLINATION_DIFFERENCE,
    Quantity.DECLINATION_SUM,
)


@pytest.fixture
def eph(fetched_ephe: Path) -> Ephemeris:
    return Ephemeris.open(fetched_ephe)


@pytest.fixture
def result(eph: Ephemeris) -> SensitivitySweep:
    return sweep(eph, RECORD, SETTINGS, SPEC_EXAMPLE)


def find(result: SensitivitySweep, quantity: Quantity, *points: object) -> MeasurementSeries:
    (series,) = (s for s in result.series if s.quantity is quantity and s.points == points)
    return series


# --- What each series must equal, read straight from the charts and comparisons ------------


def _point(chart: Chart, series: MeasurementSeries) -> ChartPoint:
    (which,) = series.points
    (point,) = (p for p in chart.points if p.point is which)
    return point


def _pair(chart: Chart, series: MeasurementSeries) -> PairMeasurement:
    (pair,) = (m for m in chart.pairs if (m.a.point, m.b.point) == series.points)
    return pair


def chart_value(chart: Chart, series: MeasurementSeries) -> float:
    match series.quantity:
        case Quantity.LONGITUDE:
            return _point(chart, series).longitude
        case Quantity.DECLINATION:
            return _point(chart, series).declination
        case Quantity.SIGNED_DIFFERENCE:
            return _pair(chart, series).longitude_difference
        case Quantity.SEPARATION:
            return _pair(chart, series).separation
        case Quantity.DEVIATION:
            (d,) = (d for d in _pair(chart, series).deviations if d.angle == series.aspect_angle)
            return d.deviation
        case Quantity.DECLINATION_DIFFERENCE:
            return _pair(chart, series).declination_difference
        case Quantity.DECLINATION_SUM:
            return _pair(chart, series).declination_sum
    raise AssertionError(series.quantity)


def comparison_change(comparison: ChartComparison, series: MeasurementSeries) -> float:
    if series.quantity in POINT_QUANTITIES:
        (point,) = (p for p in comparison.points if p.point is series.points[0])
        if series.quantity is Quantity.LONGITUDE:
            return point.longitude
        return point.declination
    (pair,) = (c for c in comparison.pairs if (c.a.a.point, c.a.b.point) == series.points)
    match series.quantity:
        case Quantity.SIGNED_DIFFERENCE:
            return pair.longitude_difference
        case Quantity.SEPARATION:
            return pair.separation
        case Quantity.DEVIATION:
            (d,) = (d for d in pair.deviations if d.angle == series.aspect_angle)
            return d.deviation
        case Quantity.DECLINATION_DIFFERENCE:
            return pair.declination_difference
        case Quantity.DECLINATION_SUM:
            return pair.declination_sum
    raise AssertionError(series.quantity)


def base_rate(chart: Chart, series: MeasurementSeries) -> float:
    match series.quantity:
        case Quantity.LONGITUDE:
            return _point(chart, series).longitude_speed
        case Quantity.DECLINATION:
            return _point(chart, series).declination_speed
        case Quantity.SIGNED_DIFFERENCE:
            pair = _pair(chart, series)
            return pair.b.longitude_speed - pair.a.longitude_speed
        case Quantity.SEPARATION:
            return _pair(chart, series).separation_rate
        case Quantity.DEVIATION:
            (d,) = (d for d in _pair(chart, series).deviations if d.angle == series.aspect_angle)
            return d.deviation_rate
        case Quantity.DECLINATION_DIFFERENCE:
            return _pair(chart, series).declination_difference_rate
        case Quantity.DECLINATION_SUM:
            return _pair(chart, series).declination_sum_rate
    raise AssertionError(series.quantity)


# --- Offsets --------------------------------------------------------------------------------


def test_offsets_are_sorted_with_zero_added(eph: Ephemeris) -> None:
    ten, five = timedelta(minutes=10), timedelta(minutes=5)
    assert sweep(eph, RECORD, SETTINGS, (ten, -five)).offsets == (-five, timedelta(0), ten)
    assert sweep(eph, RECORD, SETTINGS, (timedelta(0), ten)).offsets == (timedelta(0), ten)


def test_repeated_offsets_are_refused(eph: Ephemeris) -> None:
    with pytest.raises(InvalidOffsets, match="repeat"):
        sweep(eph, RECORD, SETTINGS, (timedelta(minutes=5), timedelta(seconds=300)))


def test_the_base_is_the_records_own_chart(eph: Ephemeris, result: SensitivitySweep) -> None:
    assert result.base == compute_chart(eph, RECORD, SETTINGS)


def test_offset_zero_compares_the_base_with_itself(result: SensitivitySweep) -> None:
    zero = result.comparisons[result.offsets.index(timedelta(0))]
    assert zero.a is result.base
    assert zero.b is result.base


def test_each_offset_is_the_chart_of_its_utc_instant_compared_with_the_base(
    eph: Ephemeris, result: SensitivitySweep
) -> None:
    assert len(result.comparisons) == len(result.offsets) == len(SPEC_EXAMPLE) + 1
    for offset, comparison in zip(result.offsets, result.comparisons, strict=True):
        shifted = BirthRecord(time=UtcTime(utc=BASE_UTC + offset), place=LONDON)
        assert comparison == compare(result.base, compute_chart(eph, shifted, SETTINGS))
        assert comparison.utc_seconds == offset.total_seconds()


# --- Series ---------------------------------------------------------------------------------


def test_series_cover_every_point_and_pair_in_chart_order(result: SensitivitySweep) -> None:
    points = [p.point for p in result.base.points]
    pairs = [(m.a.point, m.b.point) for m in result.base.pairs]
    expected: list[tuple[Quantity, tuple[object, ...], float | None]] = [
        (q, (p,), None) for p in points for q in POINT_QUANTITIES
    ]
    for pair in pairs:
        for q in PAIR_QUANTITIES:
            angles = ASPECTS if q is Quantity.DEVIATION else (None,)
            expected += [(q, pair, angle) for angle in angles]
    assert [(s.quantity, s.points, s.aspect_angle) for s in result.series] == expected
    n = len(ALL_POINTS) + 3  # the bodies and lunar points, then the three angles
    assert len(result.series) == n * 2 + math.comb(n, 2) * (4 + len(ASPECTS)) == 1530


def test_series_hold_the_chart_numbers_and_the_comparison_changes(
    result: SensitivitySweep,
) -> None:
    for series in result.series:
        charts = [c.b for c in result.comparisons]
        assert series.values == tuple(chart_value(chart, series) for chart in charts)
        assert series.changes == tuple(comparison_change(c, series) for c in result.comparisons)
        assert series.base_rate == base_rate(result.base, series)


def test_nothing_changes_at_offset_zero(result: SensitivitySweep) -> None:
    zero = result.offsets.index(timedelta(0))
    assert {s.changes[zero] for s in result.series} == {0.0}


def test_range_is_the_spread_of_the_changes(result: SensitivitySweep) -> None:
    for series in result.series:
        assert series.range == max(series.changes) - min(series.changes)


def test_series_with_an_angle_are_marked_rotation_driven(result: SensitivitySweep) -> None:
    for series in result.series:
        has_angle = any(isinstance(p, ChartAngle) for p in series.points)
        assert series.rotation_driven is has_angle, series.points


def test_by_range_runs_from_most_to_least_sensitive(result: SensitivitySweep) -> None:
    ranked = result.by_range()
    assert sorted(map(id, ranked)) == sorted(map(id, result.series))
    ranges = [s.range for s in ranked]
    assert ranges == sorted(ranges, reverse=True)
    assert ranked[0].rotation_driven
    assert not ranked[-1].rotation_driven


def test_rows_are_one_per_series_per_offset(result: SensitivitySweep) -> None:
    rows = result.rows()
    assert len(rows) == len(result.series) * len(result.offsets)
    it = iter(rows)
    for series in result.series:
        for offset, value, change in zip(
            result.offsets, series.values, series.changes, strict=True
        ):
            row = next(it)
            assert row.offset_s == offset.total_seconds()
            assert row.quantity is series.quantity
            assert row.point_a is series.points[0]
            assert row.point_b is (series.points[1] if len(series.points) == 2 else None)
            assert row.aspect_angle == series.aspect_angle
            assert row.rotation_driven is series.rotation_driven
            assert (row.value, row.change) == (value, change)


# --- Why recompute --------------------------------------------------------------------------


def test_a_pair_passing_exact_opposition_turns_inside_the_sweep(result: SensitivitySweep) -> None:
    # Measured: Saturn-Ascendant reaches exact opposition between +1 and +2 minutes. Estimating
    # from the speed at the birth time would keep the separation growing; it falls instead.
    pair = (Body.SATURN, ChartAngle.ASCENDANT)
    separation = find(result, Quantity.SEPARATION, *pair)
    assert separation.base_rate > 0
    later = [
        c
        for o, c in zip(result.offsets, separation.changes, strict=True)
        if o >= timedelta(minutes=5)
    ]
    assert all(change < 0 for change in later)
    # The signed difference wraps from +180 to -180 in value, but its change runs on smoothly.
    signed = find(result, Quantity.SIGNED_DIFFERENCE, *pair)
    assert signed.values[-1] < 0 < signed.values[0]
    assert list(signed.changes) == sorted(signed.changes)
    # The opposition is closest to exact at +1 minute, the offset nearest the moment it is exact.
    (deviation,) = (
        s
        for s in result.series
        if s.quantity is Quantity.DEVIATION and s.points == pair and s.aspect_angle == 180.0
    )
    closest = max(range(len(result.offsets)), key=lambda i: deviation.values[i])
    assert result.offsets[closest] == timedelta(minutes=1)


# --- Time handling --------------------------------------------------------------------------


def test_offsets_shift_the_utc_instant_across_a_clock_change(eph: Ephemeris) -> None:
    # British Summer Time began at 01:00 UTC on 1987-03-29: 00:55 GMT plus ten minutes of
    # wall clock would be 01:05, which never happened. The UTC instant moves on regardless.
    base = ZonedLocalTime(local=datetime(1987, 3, 29, 0, 55), zone="Europe/London")
    with pytest.raises(NonexistentLocalTime):
        resolve(ZonedLocalTime(local=datetime(1987, 3, 29, 1, 5), zone="Europe/London"), LONDON)
    result = sweep(eph, BirthRecord(time=base, place=LONDON), SETTINGS, (timedelta(minutes=10),))
    later = result.comparisons[-1]
    assert later.utc_seconds == 600.0
    assert later.b.time.instant.utc == datetime(1987, 3, 29, 1, 5, tzinfo=UTC)


def test_a_leap_second_inside_the_sweep_shows_in_tt_seconds(eph: Ephemeris) -> None:
    record = BirthRecord(
        time=UtcTime(utc=datetime(2016, 12, 31, 23, 59, 30, tzinfo=UTC)), place=LONDON
    )
    settings = ChartSettings(time_scale=TimeScale.UT1_FROM_DELTA_T, aspect_angles=MAJOR)
    later = sweep(eph, record, settings, (timedelta(seconds=60),)).comparisons[-1]
    assert later.utc_seconds == 60.0
    jd_rounding_s = 4 * math.ulp(later.a.time.jd_tt) * 86400
    assert later.tt_seconds == pytest.approx(61.0, abs=jd_rounding_s)


def test_an_ambiguous_base_time_is_refused(eph: Ephemeris) -> None:
    record = BirthRecord(
        time=ZonedLocalTime(local=datetime(1987, 10, 25, 1, 30), zone="Europe/London"),
        place=LONDON,
    )
    with pytest.raises(AmbiguousLocalTime):
        sweep(eph, record, SETTINGS, SPEC_EXAMPLE)


# --- Known discontinuity --------------------------------------------------------------------


def test_the_switch_dates_are_swiss_ephemeris_own() -> None:
    # SIDT_LTERM_T0 and SIDT_LTERM_T1 in swephlib.c (2.10.03); the long-term model applies at
    # jd_ut1 <= T0 and jd_ut1 >= T1.
    assert SIDEREAL_TIME_SWITCHES_JD_UT1 == (2396758.5, 2469807.5)


@pytest.mark.parametrize(
    ("base", "offsets", "crosses"),
    [
        (datetime(1849, 12, 31, 23, 55), (-10, 10), True),
        (datetime(1850, 1, 1, 0, 15), (-10, 10), False),
        (datetime(1849, 12, 31, 23, 40), (-10, 10), False),
        # Exactly at 1850-01-01 00:00 UT1 Swiss Ephemeris still uses the long-term model.
        (datetime(1850, 1, 1, 0, 0), (10,), True),
        (datetime(1850, 1, 1, 0, 0), (-10,), False),
        (datetime(2049, 12, 31, 23, 55), (-10, 10), True),
        # At exactly 2050-01-01 00:00 UT1 it has already switched to the long-term model.
        (datetime(2050, 1, 1, 0, 0), (-10,), True),
        (datetime(2050, 1, 1, 0, 0), (10,), False),
        (datetime(1987, 6, 15, 3, 25), (-10, 10), False),
    ],
)
def test_a_sweep_across_the_sidereal_time_switch_is_flagged(
    eph: Ephemeris, base: datetime, offsets: tuple[int, ...], crosses: bool
) -> None:
    # The angles jump there (measured: 97 mas at 1850, 1.9 arcsec at 2050). Under
    # CLOCK_AS_UT1 the UTC clock reading is the UT1 that the switch is defined in.
    record = BirthRecord(time=UtcTime(utc=base.replace(tzinfo=UTC)), place=LONDON)
    minutes = tuple(timedelta(minutes=m) for m in offsets)
    assert sweep(eph, record, SETTINGS, minutes).crosses_sidereal_time_switch is crosses
