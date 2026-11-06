"""Measurements: raw separations, deviations from exact aspects, parallels and their rates.

No orbs, no aspect flags, no applying/separating labels, no scores: only continuous numbers that
any later rule can be applied to. The core tests use hand-built points and need no ephemeris.
"""

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import swisseph

from astro_research.astronomical_time import AstronomicalTime, TimeScale
from astro_research.civil_time import UtcTime, resolve
from astro_research.ephemeris import Ephemeris
from astro_research.geo import GeoPosition
from astro_research.measurements import (
    MAJOR,
    OCTILE,
    ChartAngle,
    ChartPoint,
    PairMeasurement,
    chart_points,
    measure,
)
from astro_research.positions import ALL_POINTS, Body, LunarPoint

LONDON = GeoPosition(latitude=51.5074, longitude=-0.1278)


def point(
    which: Body | LunarPoint | ChartAngle,
    longitude: float,
    *,
    latitude: float = 0.0,
    declination: float = 0.0,
    longitude_speed: float = 0.0,
    declination_speed: float = 0.0,
) -> ChartPoint:
    return ChartPoint(
        point=which,
        longitude=longitude,
        latitude=latitude,
        declination=declination,
        longitude_speed=longitude_speed,
        declination_speed=declination_speed,
    )


def pair(a: ChartPoint, b: ChartPoint, angles: tuple[float, ...] = MAJOR) -> PairMeasurement:
    (only,) = measure([a, b], angles)
    return only


# --- Longitude separation -----------------------------------------------------------------


def test_presets() -> None:
    assert MAJOR == (0.0, 60.0, 90.0, 120.0, 180.0)
    assert OCTILE == (45.0, 90.0, 135.0, 180.0)


def test_separation_wraps_through_zero_aries() -> None:
    m = pair(point(Body.SUN, 359.0), point(Body.MOON, 1.0))
    assert m.longitude_difference == pytest.approx(2.0)
    assert m.separation == pytest.approx(2.0)


def test_signed_difference_is_b_minus_a() -> None:
    m = pair(point(Body.SUN, 10.0), point(Body.MOON, 350.0))
    assert m.longitude_difference == pytest.approx(-20.0)
    assert m.separation == pytest.approx(20.0)


def test_exact_opposition_is_plus_180_in_either_order() -> None:
    # The signed difference lies in (-180, 180]: an exact opposition is +180 both ways.
    assert pair(point(Body.SUN, 0.0), point(Body.MOON, 180.0)).longitude_difference == 180.0
    assert pair(point(Body.SUN, 180.0), point(Body.MOON, 0.0)).longitude_difference == 180.0


def test_swapping_points_flips_the_signed_difference() -> None:
    a, b = point(Body.SUN, 12.5), point(Body.MOON, 207.25)
    forward, backward = pair(a, b), pair(b, a)
    assert forward.longitude_difference == pytest.approx(-backward.longitude_difference)
    assert forward.separation == pytest.approx(backward.separation)


def test_shifting_every_longitude_changes_nothing() -> None:
    points = [point(Body.SUN, 3.0), point(Body.MOON, 97.0), point(Body.MARS, 250.0)]
    shifted = [replace(p, longitude=(p.longitude + 123.456) % 360) for p in points]
    for m, s in zip(measure(points, OCTILE), measure(shifted, OCTILE), strict=True):
        assert m.separation == pytest.approx(s.separation)
        assert [d.deviation for d in m.deviations] == pytest.approx(
            [d.deviation for d in s.deviations]
        )


# --- Deviations from exact aspects --------------------------------------------------------


@pytest.mark.parametrize(
    ("separation", "angle", "expected"),
    [
        (92.0, 90.0, +2.0),
        (88.0, 90.0, -2.0),
        (120.0, 120.0, 0.0),
        (3.0, 0.0, 3.0),
        (178.0, 180.0, -2.0),
    ],
)
def test_deviation_is_separation_minus_angle(
    separation: float, angle: float, expected: float
) -> None:
    m = pair(point(Body.SUN, 0.0), point(Body.MOON, separation), (angle,))
    (deviation,) = m.deviations
    assert deviation.angle == angle
    assert deviation.deviation == pytest.approx(expected)


def test_every_requested_angle_once_per_pair_in_order() -> None:
    points = [point(Body.SUN, 0.0), point(Body.MOON, 50.0), point(Body.MARS, 140.0)]
    measurements = measure(points, OCTILE)
    assert [(m.a.point, m.b.point) for m in measurements] == [
        (Body.SUN, Body.MOON),
        (Body.SUN, Body.MARS),
        (Body.MOON, Body.MARS),
    ]
    for m in measurements:
        assert tuple(d.angle for d in m.deviations) == OCTILE


@pytest.mark.parametrize("bad", [(-1.0,), (181.0,), (90.0, 90.0)])
def test_aspect_angles_must_be_distinct_and_within_0_to_180(bad: tuple[float, ...]) -> None:
    with pytest.raises(ValueError):
        measure([point(Body.SUN, 0.0), point(Body.MOON, 10.0)], bad)


# --- Rates ----------------------------------------------------------------------------------


def test_separation_rate_comes_from_the_two_speeds() -> None:
    # The Moon 30 degrees ahead of the Sun and gaining 12 deg/day: the separation widens.
    ahead = pair(
        point(Body.SUN, 100.0, longitude_speed=1.0), point(Body.MOON, 130.0, longitude_speed=13.0)
    )
    assert ahead.separation_rate == pytest.approx(12.0)
    # The Moon 30 degrees behind and gaining: the separation closes.
    behind = pair(
        point(Body.SUN, 100.0, longitude_speed=1.0), point(Body.MOON, 70.0, longitude_speed=13.0)
    )
    assert behind.separation_rate == pytest.approx(-12.0)
    for m in (ahead, behind):
        assert all(d.deviation_rate == m.separation_rate for d in m.deviations)


# --- Great-circle separation --------------------------------------------------------------


def test_great_circle_separation_includes_latitude() -> None:
    same_longitude = pair(point(Body.SUN, 40.0), point(Body.MOON, 40.0, latitude=5.0))
    assert same_longitude.separation == pytest.approx(0.0)
    assert same_longitude.great_circle_separation == pytest.approx(5.0)
    on_ecliptic = pair(point(Body.SUN, 40.0), point(Body.MOON, 100.0))
    assert on_ecliptic.great_circle_separation == pytest.approx(60.0)
    # Spherical law of cosines for a general case.
    m = pair(point(Body.SUN, 10.0, latitude=-3.0), point(Body.PLUTO, 75.0, latitude=17.0))
    lat_a, lat_b, dlon = math.radians(-3.0), math.radians(17.0), math.radians(65.0)
    cos_sep = math.sin(lat_a) * math.sin(lat_b) + math.cos(lat_a) * math.cos(lat_b) * math.cos(dlon)
    assert m.great_circle_separation == pytest.approx(math.degrees(math.acos(cos_sep)), abs=1e-9)


# --- Parallels ----------------------------------------------------------------------------


def test_parallel_and_contra_parallel_measures() -> None:
    parallel = pair(
        point(Body.SUN, 0.0, declination=20.0, declination_speed=0.2),
        point(Body.MARS, 50.0, declination=20.5, declination_speed=-0.1),
    )
    assert parallel.declination_difference == pytest.approx(0.5)
    assert parallel.declination_sum == pytest.approx(40.5)
    assert parallel.declination_difference_rate == pytest.approx(-0.3)
    assert parallel.declination_sum_rate == pytest.approx(0.1)
    contra = pair(point(Body.SUN, 0.0, declination=20.0), point(Body.MARS, 50.0, declination=-20.3))
    assert contra.declination_difference == pytest.approx(-40.3)
    assert contra.declination_sum == pytest.approx(-0.3)


# --- Chart points from a real chart -------------------------------------------------------


@pytest.fixture
def eph(fetched_ephe: Path) -> Ephemeris:
    return Ephemeris.open(fetched_ephe)


def at(eph: Ephemeris, utc: datetime) -> AstronomicalTime:
    return eph.time(resolve(UtcTime(utc=utc), LONDON), TimeScale.CLOCK_AS_UT1)


def test_chart_angles_are_ascendant_midheaven_and_vertex_only() -> None:
    # The Descendant and IC duplicate the Ascendant and MC (a conjunction with one is an
    # opposition to the other), so they would add correlated measurements.
    assert list(ChartAngle) == [ChartAngle.ASCENDANT, ChartAngle.MIDHEAVEN, ChartAngle.VERTEX]


def test_chart_points_are_the_positions_then_the_three_angles(eph: Ephemeris) -> None:
    time = at(eph, datetime(1987, 6, 15, 3, 25, tzinfo=UTC))
    points = chart_points(eph.positions(time, ALL_POINTS), eph.angles(time, LONDON))
    assert [p.point for p in points] == [*ALL_POINTS, *ChartAngle]


def test_body_points_carry_the_position_values(eph: Ephemeris) -> None:
    time = at(eph, datetime(1987, 6, 15, 3, 25, tzinfo=UTC))
    positions = eph.positions(time, ALL_POINTS)
    for p in chart_points(positions, eph.angles(time, LONDON))[: len(ALL_POINTS)]:
        source = positions[p.point]  # type: ignore[index]
        assert (p.longitude, p.latitude, p.declination) == (
            source.longitude,
            source.latitude,
            source.declination,
        )
        assert (p.longitude_speed, p.declination_speed) == (
            source.longitude_speed,
            source.declination_speed,
        )


def test_angle_declinations_follow_from_the_true_obliquity(eph: Ephemeris) -> None:
    time = at(eph, datetime(1987, 6, 15, 3, 25, tzinfo=UTC))
    angles = eph.angles(time, LONDON)
    by_angle = {p.point: p for p in chart_points(eph.positions(time), angles)}
    for which, longitude in (
        (ChartAngle.ASCENDANT, angles.ascendant),
        (ChartAngle.MIDHEAVEN, angles.midheaven),
        (ChartAngle.VERTEX, angles.vertex),
    ):
        _, dec, _ = swisseph.cotrans((longitude, 0.0, 1.0), -angles.true_obliquity)
        assert by_angle[which].latitude == 0.0
        assert by_angle[which].declination == pytest.approx(dec, abs=1e-9)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (Body.SUN, Body.MOON),
        (ChartAngle.ASCENDANT, Body.MOON),
        (ChartAngle.MIDHEAVEN, LunarPoint.TRUE_NODE),
        (Body.MARS, Body.VENUS),
    ],
)
def test_rates_match_the_change_on_a_real_chart(
    eph: Ephemeris, a: Body | LunarPoint | ChartAngle, b: Body | LunarPoint | ChartAngle
) -> None:
    def measured(utc: datetime) -> PairMeasurement:
        time = at(eph, utc)
        points = {
            p.point: p
            for p in chart_points(eph.positions(time, ALL_POINTS), eph.angles(time, LONDON))
        }
        return pair(points[a], points[b])

    now_utc = datetime(1987, 6, 15, 3, 25, tzinfo=UTC)
    before, now, after = (measured(now_utc + timedelta(minutes=m)) for m in (-1, 0, 1))
    per_day = 1 / (2 / 1440)
    assert (after.separation - before.separation) * per_day == pytest.approx(
        now.separation_rate, rel=1e-3
    )
    assert (
        after.declination_difference - before.declination_difference
    ) * per_day == pytest.approx(now.declination_difference_rate, rel=1e-3, abs=1e-6)
