"""Two-chart comparison: chart B minus chart A, for every number the charts hold.

Both charts travel with the comparison, so absolute values and differences are read side by
side. Changes are continuous numbers; nothing says a contact was gained or lost, because that
would need an orb. The records are synthetic twins a minute apart.
"""

import math
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from astro_research.astronomical_time import TimeScale
from astro_research.chart import BirthRecord, Chart, ChartSettings, TimeResolution, compute_chart
from astro_research.civil_time import UtcTime, ZonedLocalTime
from astro_research.comparison import ChartComparison, ChartsNotComparable, compare
from astro_research.ephemeris import Ephemeris
from astro_research.geo import GeoPosition
from astro_research.measurements import MAJOR, OCTILE, ChartAngle
from astro_research.positions import ALL_POINTS, Body, LunarPoint

LONDON = GeoPosition(latitude=51.5074, longitude=-0.1278)
PARIS = GeoPosition(latitude=48.8566, longitude=2.3522)
TWIN_A = BirthRecord(
    time=ZonedLocalTime(local=datetime(1987, 6, 15, 4, 25), zone="Europe/London"),
    place=LONDON,
    time_resolution=TimeResolution.MINUTE,
)
TWIN_B = replace(
    TWIN_A, time=ZonedLocalTime(local=datetime(1987, 6, 15, 4, 26), zone="Europe/London")
)
SETTINGS = ChartSettings(
    time_scale=TimeScale.CLOCK_AS_UT1, aspect_angles=tuple(sorted({*MAJOR, *OCTILE}))
)
# Float64 arithmetic on SE's own ARMC, as in test_angles: 1e-9 degree is 3.6 micro-arcseconds.
DEFINITION_TOLERANCE_DEG = 1e-9


@pytest.fixture
def eph(fetched_ephe: Path) -> Ephemeris:
    return Ephemeris.open(fetched_ephe)


@pytest.fixture
def twins(eph: Ephemeris) -> tuple[Chart, Chart]:
    return compute_chart(eph, TWIN_A, SETTINGS), compute_chart(eph, TWIN_B, SETTINGS)


def wrap(deg: float) -> float:
    """Into (-180, 180]."""
    if deg > 180.0:
        return deg - 360.0
    if deg <= -180.0:
        return deg + 360.0
    return deg


def pair_numbers(comparison: ChartComparison) -> list[float]:
    numbers: list[float] = []
    for change in comparison.pairs:
        numbers += [
            change.longitude_difference,
            change.separation,
            change.great_circle_separation,
            change.declination_difference,
            change.declination_sum,
            *(d.deviation for d in change.deviations),
        ]
    return numbers


def point_numbers(comparison: ChartComparison) -> list[float]:
    numbers: list[float] = []
    for change in comparison.points:
        numbers += [
            change.longitude,
            change.latitude,
            change.declination,
            change.longitude_speed,
            change.declination_speed,
        ]
    return numbers


# --- What a comparison holds ----------------------------------------------------------------


def test_a_chart_compared_with_itself_differs_by_exactly_zero(eph: Ephemeris) -> None:
    chart = compute_chart(eph, TWIN_A, SETTINGS)
    comparison = compare(chart, chart)
    assert (comparison.utc_seconds, comparison.tt_seconds, comparison.armc) == (0.0, 0.0, 0.0)
    assert set(point_numbers(comparison)) == {0.0}
    assert set(pair_numbers(comparison)) == {0.0}


def test_both_charts_travel_with_the_comparison(twins: tuple[Chart, Chart]) -> None:
    a, b = twins
    comparison = compare(a, b)
    assert comparison.a is a
    assert comparison.b is b
    for change, pa, pb in zip(comparison.points, a.points, b.points, strict=True):
        assert change.point is pa.point is pb.point
        assert (change.a, change.b) == (pa, pb)
    for pair_change, ma, mb in zip(comparison.pairs, a.pairs, b.pairs, strict=True):
        assert (pair_change.a, pair_change.b) == (ma, mb)
        for d, da, db in zip(pair_change.deviations, ma.deviations, mb.deviations, strict=True):
            assert d.angle == da.angle == db.angle
            assert (d.a, d.b) == (da, db)


def test_every_change_is_b_minus_a(twins: tuple[Chart, Chart]) -> None:
    a, b = twins
    comparison = compare(a, b)
    assert comparison.utc_seconds == (b.time.instant.utc - a.time.instant.utc).total_seconds()
    assert comparison.tt_seconds == (b.time.jd_tt - a.time.jd_tt) * 86400
    assert comparison.armc == wrap(b.angles.armc - a.angles.armc)
    for change in comparison.points:
        pa, pb = change.a, change.b
        assert change.longitude == wrap(pb.longitude - pa.longitude)
        assert change.latitude == pb.latitude - pa.latitude
        assert change.declination == pb.declination - pa.declination
        assert change.longitude_speed == pb.longitude_speed - pa.longitude_speed
        assert change.declination_speed == pb.declination_speed - pa.declination_speed
    for pair_change in comparison.pairs:
        ma, mb = pair_change.a, pair_change.b
        assert pair_change.longitude_difference == wrap(
            mb.longitude_difference - ma.longitude_difference
        )
        assert pair_change.separation == mb.separation - ma.separation
        assert pair_change.great_circle_separation == (
            mb.great_circle_separation - ma.great_circle_separation
        )
        assert pair_change.declination_difference == (
            mb.declination_difference - ma.declination_difference
        )
        assert pair_change.declination_sum == mb.declination_sum - ma.declination_sum
        for d in pair_change.deviations:
            assert d.deviation == d.b.deviation - d.a.deviation


def test_each_deviation_changes_by_the_change_in_separation(twins: tuple[Chart, Chart]) -> None:
    # deviation = separation - angle, so every aspect angle's deviation shifts by the same amount;
    # the tolerance is the rounding of three subtractions of degree values below 360.
    rounding = 4 * math.ulp(360.0)
    for pair_change in compare(*twins).pairs:
        for d in pair_change.deviations:
            assert d.deviation == pytest.approx(pair_change.separation, abs=rounding)


def test_swapping_the_charts_negates_every_change(twins: tuple[Chart, Chart]) -> None:
    a, b = twins
    forward, backward = compare(a, b), compare(b, a)
    assert backward.utc_seconds == -forward.utc_seconds
    assert backward.tt_seconds == -forward.tt_seconds
    assert backward.armc == -forward.armc
    assert point_numbers(backward) == [-x for x in point_numbers(forward)]
    assert pair_numbers(backward) == [-x for x in pair_numbers(forward)]


def test_longitude_changes_wrap_through_zero_aries(eph: Ephemeris) -> None:
    chart = compute_chart(eph, TWIN_A, SETTINGS)
    first = chart.points[0]
    before = replace(chart, points=(replace(first, longitude=359.9), *chart.points[1:]))
    after = replace(chart, points=(replace(first, longitude=0.1), *chart.points[1:]))
    assert compare(before, after).points[0].longitude == pytest.approx(0.2)
    assert compare(after, before).points[0].longitude == pytest.approx(-0.2)


def test_a_half_turn_reads_as_plus_180_either_way(eph: Ephemeris) -> None:
    # The same (-180, 180] convention as a pair's signed difference.
    chart = compute_chart(eph, TWIN_A, SETTINGS)
    first = chart.points[0]
    at_0 = replace(chart, points=(replace(first, longitude=0.0), *chart.points[1:]))
    at_180 = replace(chart, points=(replace(first, longitude=180.0), *chart.points[1:]))
    assert compare(at_0, at_180).points[0].longitude == 180.0
    assert compare(at_180, at_0).points[0].longitude == 180.0


def test_the_armc_change_wraps_through_zero(eph: Ephemeris) -> None:
    chart = compute_chart(eph, TWIN_A, SETTINGS)
    before = replace(chart, angles=replace(chart.angles, armc=359.9))
    after = replace(chart, angles=replace(chart.angles, armc=0.1))
    assert compare(before, after).armc == pytest.approx(0.2)
    assert compare(after, before).armc == pytest.approx(-0.2)


def test_signed_differences_wrap_through_the_opposition(eph: Ephemeris) -> None:
    # A pair's signed difference jumps from +180 to -180 as it passes exact opposition.
    chart = compute_chart(eph, TWIN_A, SETTINGS)
    first = chart.pairs[0]
    before = replace(chart, pairs=(replace(first, longitude_difference=179.9), *chart.pairs[1:]))
    after = replace(chart, pairs=(replace(first, longitude_difference=-179.9), *chart.pairs[1:]))
    assert compare(before, after).pairs[0].longitude_difference == pytest.approx(0.2)


def test_angle_contacts_are_the_pairs_that_involve_an_angle(twins: tuple[Chart, Chart]) -> None:
    comparison = compare(*twins)
    contacts = comparison.involving_angles()
    # 14 bodies and lunar points with each of 3 angles, plus the 3 angle-angle pairs.
    assert len(comparison.pairs) == math.comb(len(ALL_POINTS) + 3, 2)
    assert len(contacts) == len(ALL_POINTS) * 3 + 3
    for change in contacts:
        assert ChartAngle in (type(change.a.a.point), type(change.a.b.point))
    assert set(map(id, contacts)) <= set(map(id, comparison.pairs))


# --- Twins a minute apart -------------------------------------------------------------------


def test_twins_a_second_apart(eph: Ephemeris) -> None:
    # Records to the second: 04:25:00 and 04:25:01 BST.
    second = replace(
        TWIN_A,
        time=ZonedLocalTime(local=datetime(1987, 6, 15, 4, 25, 1), zone="Europe/London"),
        time_resolution=TimeResolution.SECOND,
    )
    a = compute_chart(eph, replace(TWIN_A, time_resolution=TimeResolution.SECOND), SETTINGS)
    b = compute_chart(eph, second, SETTINGS)
    comparison = compare(a, b)
    assert comparison.utc_seconds == 1.0
    jd_rounding_s = 4 * math.ulp(a.time.jd_tt) * 86400
    assert comparison.tt_seconds == pytest.approx(1.0, abs=jd_rounding_s)
    # Over one second, the Ascendant's mean speed, taken over the UT1 interval the two charts
    # were computed at, lies between its speeds at the two ends (measured: half-way for this
    # record; 0.35-0.53 of the way over 12 cases). The interval must be the charts' own: one
    # second carries Julian-date rounding of a few microseconds, a 5e-6 relative error. The MC,
    # Vertex and Moon change speed by less than Swiss Ephemeris's speed precision in one second
    # (measured outside the band in 1-4 of 12 cases), so a minute is needed for them.
    (ascendant,) = (p for p in comparison.points if p.point is ChartAngle.ASCENDANT)
    mean_speed = ascendant.longitude / (b.time.jd_ut1 - a.time.jd_ut1)
    low, high = sorted((ascendant.a.longitude_speed, ascendant.b.longitude_speed))
    assert low < mean_speed < high


def test_twins_a_minute_apart_are_sixty_seconds_apart(twins: tuple[Chart, Chart]) -> None:
    a, b = twins
    comparison = compare(a, b)
    assert comparison.utc_seconds == 60.0
    # Each chart's TT Julian date carries up to two roundings (the date, then delta-T added).
    jd_rounding_s = 4 * math.ulp(a.time.jd_tt) * 86400
    assert comparison.tt_seconds == pytest.approx(60.0, abs=jd_rounding_s)


@pytest.mark.parametrize(
    ("time_scale", "elapsed_s"),
    [(TimeScale.UT1_FROM_DELTA_T, 61.0), (TimeScale.CLOCK_AS_UT1, 60.0)],
    ids=["ut1-from-delta-t", "clock-as-ut1"],
)
def test_a_leap_second_counts_in_tt_but_not_in_utc_seconds(
    eph: Ephemeris, time_scale: TimeScale, elapsed_s: float
) -> None:
    # 2016-12-31 ended with 23:59:60. Taking the clock reading as UT1 skips that second.
    settings = replace(SETTINGS, time_scale=time_scale)
    before, after = (
        compute_chart(eph, BirthRecord(time=UtcTime(utc=utc), place=LONDON), settings)
        for utc in (
            datetime(2016, 12, 31, 23, 59, 30, tzinfo=UTC),
            datetime(2017, 1, 1, 0, 0, 30, tzinfo=UTC),
        )
    )
    comparison = compare(before, after)
    assert comparison.utc_seconds == 60.0
    jd_rounding_s = 4 * math.ulp(before.time.jd_tt) * 86400
    assert comparison.tt_seconds == pytest.approx(elapsed_s, abs=jd_rounding_s)


@pytest.mark.parametrize(
    "which", [ChartAngle.ASCENDANT, ChartAngle.MIDHEAVEN, ChartAngle.VERTEX, Body.MOON]
)
def test_a_minute_moves_each_fast_point_at_a_speed_between_its_end_speeds(
    twins: tuple[Chart, Chart], which: ChartAngle | Body
) -> None:
    # The minute's mean speed is the speed at some moment within it (mean value theorem), and
    # these points' speeds change monotonically over a minute, so it lies between the speeds at
    # the two ends. Measured for this record: it sits 0.50-0.51 of the way from A's to B's.
    # Slow planets cannot be checked this way: Swiss Ephemeris's apparent speeds differ from
    # the change in position by up to 3.9e-6 deg/day (Pluto, measured), more than their speeds
    # change in a minute.
    comparison = compare(*twins)
    (change,) = (p for p in comparison.points if p.point is which)
    mean_speed = change.longitude / (comparison.utc_seconds / 86400)
    low, high = sorted((change.a.longitude_speed, change.b.longitude_speed))
    assert low < mean_speed < high


def test_the_same_moment_in_two_places_moves_only_the_angles(eph: Ephemeris) -> None:
    # Positions are geocentric, so only the angles depend on the place. ARMC is local sidereal
    # time, which moves by exactly the difference in longitude.
    comparison = compare(
        compute_chart(eph, TWIN_A, SETTINGS),
        compute_chart(eph, replace(TWIN_A, place=PARIS), SETTINGS),
    )
    assert comparison.utc_seconds == comparison.tt_seconds == 0.0
    assert comparison.armc == pytest.approx(
        PARIS.longitude - LONDON.longitude, abs=DEFINITION_TOLERANCE_DEG
    )
    for change in comparison.points:
        moved = change.longitude != 0.0
        assert moved is isinstance(change.point, ChartAngle), change.point


# --- Refusals -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "other",
    [
        replace(SETTINGS, time_scale=TimeScale.UT1_FROM_DELTA_T),
        replace(SETTINGS, aspect_angles=MAJOR),
        replace(SETTINGS, points=(Body.SUN, Body.MOON, LunarPoint.MEAN_NODE)),
    ],
    ids=["time-scale", "aspect-angles", "points"],
)
def test_charts_under_different_settings_are_not_compared(
    eph: Ephemeris, other: ChartSettings
) -> None:
    a = compute_chart(eph, TWIN_A, SETTINGS)
    b = compute_chart(eph, TWIN_B, other)
    with pytest.raises(ChartsNotComparable, match="settings"):
        compare(a, b)


def test_charts_from_different_ephemerides_are_not_compared(eph: Ephemeris) -> None:
    a = compute_chart(eph, TWIN_A, SETTINGS)
    other = replace(a.positions.ephemeris, de_number=431)
    for b in (
        replace(a, positions=replace(a.positions, ephemeris=other)),
        replace(a, angles=replace(a.angles, ephemeris=other)),
    ):
        with pytest.raises(ChartsNotComparable, match="ephemeris"):
            compare(a, b)


def test_charts_under_different_conventions_are_not_compared(eph: Ephemeris) -> None:
    a = compute_chart(eph, TWIN_A, SETTINGS)
    for b in (
        replace(a, positions=replace(a.positions, conventions="positions-v1: older")),
        replace(a, angles=replace(a.angles, conventions="angles-v0: older")),
        replace(a, measurement_conventions="measurements-v0: older"),
    ):
        with pytest.raises(ChartsNotComparable, match="conventions"):
            compare(a, b)


def test_charts_whose_points_or_pairs_do_not_line_up_are_not_compared(eph: Ephemeris) -> None:
    # Only hand-edited charts can disagree here; computed ones line up whenever settings match.
    a = compute_chart(eph, TWIN_A, SETTINGS)
    for b in (
        replace(a, points=tuple(reversed(a.points))),
        replace(a, pairs=tuple(reversed(a.pairs))),
    ):
        with pytest.raises(ChartsNotComparable, match="line up"):
            compare(a, b)
