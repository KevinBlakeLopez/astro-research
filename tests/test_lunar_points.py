"""Lunar points: mean and true node, mean and osculating apogee, through `positions()`.

They come back in the same `BodyPosition` shape as the bodies, so later aspect and parallel code
can treat points and planets alike. Definitions (Swiss Ephemeris manual §2.2):
- mean node and mean apogee: Moshier's adjustment of ELP2000-85 with DE431 corrections; the mean
  apogee lies on the inclined mean orbit, projected onto the ecliptic, so it has a latitude;
- true node and osculating apogee: osculating elements of the Moon's momentary orbit, computed
  from its position and velocity.
`distance_au` for these points is Swiss Ephemeris's orbital distance, with no research meaning.

Independent validation against ERFA and Horizons is layer 4 (tests/test_reference_lunar.py).
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import swisseph
from reference_lunar import mean_apogee, mean_node

from astro_research.astronomical_time import AstronomicalTime, TimeScale
from astro_research.civil_time import UtcTime, resolve
from astro_research.ephemeris import Ephemeris
from astro_research.geo import GeoPosition
from astro_research.positions import ALL_POINTS, Body, LunarPoint

SE_POINTS = {
    LunarPoint.MEAN_NODE: swisseph.MEAN_NODE,
    LunarPoint.TRUE_NODE: swisseph.TRUE_NODE,
    LunarPoint.MEAN_APOGEE: swisseph.MEAN_APOG,
    LunarPoint.OSCULATING_APOGEE: swisseph.OSCU_APOG,
}


@pytest.fixture
def eph(fetched_ephe: Path) -> Ephemeris:
    return Ephemeris.open(fetched_ephe)


def at(eph: Ephemeris, year: int, month: int, day: int, hour: int = 12) -> AstronomicalTime:
    utc = datetime(year, month, day, hour, tzinfo=UTC)
    return eph.time(
        resolve(UtcTime(utc=utc), GeoPosition(latitude=0.0, longitude=0.0)), TimeScale.CLOCK_AS_UT1
    )


# --- Shape --------------------------------------------------------------------------------


def test_points_and_bodies_can_be_requested_together(eph: Ephemeris) -> None:
    positions = eph.positions(at(eph, 2000, 1, 1), [LunarPoint.TRUE_NODE, Body.MOON])
    assert [p.body for p in positions.bodies] == [LunarPoint.TRUE_NODE, Body.MOON]
    assert positions[LunarPoint.TRUE_NODE].body is LunarPoint.TRUE_NODE


def test_default_is_still_the_ten_bodies(eph: Ephemeris) -> None:
    assert [p.body for p in eph.positions(at(eph, 2000, 1, 1)).bodies] == list(Body)


def test_all_points_is_bodies_then_lunar_points(eph: Ephemeris) -> None:
    assert (*Body, *LunarPoint) == ALL_POINTS
    assert len(eph.positions(at(eph, 2000, 1, 1), ALL_POINTS).bodies) == 14


@pytest.mark.parametrize("point", list(LunarPoint))
def test_values_are_swiss_ephemeris_defaults_unrounded(eph: Ephemeris, point: LunarPoint) -> None:
    time = at(eph, 1987, 6, 15, 3)
    p = eph.positions(time, [point])[point]
    ecl, _, _ = swisseph.calc(
        time.jd_tt, SE_POINTS[point], swisseph.FLG_SWIEPH | swisseph.FLG_SPEED
    )
    equ, _, _ = swisseph.calc(
        time.jd_tt,
        SE_POINTS[point],
        swisseph.FLG_SWIEPH | swisseph.FLG_EQUATORIAL | swisseph.FLG_SPEED,
    )
    assert (p.longitude, p.latitude, p.distance_au) == tuple(ecl[:3])
    assert (p.longitude_speed, p.latitude_speed, p.distance_speed) == tuple(ecl[3:])
    assert (p.right_ascension, p.declination) == tuple(equ[:2])
    assert (p.right_ascension_speed, p.declination_speed) == tuple(equ[3:5])


# --- Latitudes ----------------------------------------------------------------------------


@pytest.mark.parametrize("point", [LunarPoint.MEAN_NODE, LunarPoint.TRUE_NODE])
def test_nodes_lie_on_the_ecliptic(eph: Ephemeris, point: LunarPoint) -> None:
    for month in (1, 4, 7, 10):
        assert eph.positions(at(eph, 2024, month, 1), [point])[point].latitude == 0.0


@pytest.mark.parametrize("point", [LunarPoint.MEAN_APOGEE, LunarPoint.OSCULATING_APOGEE])
def test_apogees_lie_on_the_inclined_lunar_orbit(eph: Ephemeris, point: LunarPoint) -> None:
    # The lunar orbit is inclined about 5.1 degrees (osculating up to ~5.3) to the ecliptic.
    latitudes = [
        abs(eph.positions(at(eph, 2024, m, 1), [point])[point].latitude) for m in range(1, 13)
    ]
    assert max(latitudes) <= 5.35
    assert max(latitudes) > 0.5  # not flattened onto the ecliptic


# --- Speeds -------------------------------------------------------------------------------


def reference_rate_deg_per_day(point: LunarPoint, jd_tt: float) -> float:
    """Rate of the ERFA-based reference longitude (with nutation), central difference over a day."""

    def longitude(jd: float) -> float:
        return mean_node(jd) if point is LunarPoint.MEAN_NODE else mean_apogee(jd)[0]

    return (longitude(jd_tt + 0.5) - longitude(jd_tt - 0.5) + 180) % 360 - 180


@pytest.mark.parametrize("point", [LunarPoint.MEAN_NODE, LunarPoint.MEAN_APOGEE])
@pytest.mark.parametrize("year", [1850, 2000, 2100])
def test_mean_points_move_at_the_iers_rates(eph: Ephemeris, point: LunarPoint, year: int) -> None:
    # About -0.05295 deg/day for the node (one turn in 18.6 years) and +0.1114 for the apogee
    # (8.85 years). The apogee is projected from the inclined orbit onto the ecliptic, which
    # changes its rate by up to ~0.5% over the cycle, so it is compared with the projected
    # reference rather than with the raw perigee rate.
    time = at(eph, year, 6, 1)
    speed = eph.positions(time, [point])[point].longitude_speed
    assert speed == pytest.approx(reference_rate_deg_per_day(point, time.jd_tt), abs=1e-4)


@pytest.mark.parametrize("point", list(LunarPoint))
def test_speed_matches_the_change_in_position(eph: Ephemeris, point: LunarPoint) -> None:
    # 2000-01-01 is 17 days from the nearest segment boundary of the compressed Moon file.
    before = eph.positions(at(eph, 2000, 1, 1, 11), [point])[point]
    now = eph.positions(at(eph, 2000, 1, 1, 12), [point])[point]
    after = eph.positions(at(eph, 2000, 1, 1, 13), [point])[point]
    change = ((after.longitude - before.longitude + 180) % 360 - 180) / (2 / 24)
    assert change == pytest.approx(now.longitude_speed, rel=1e-2, abs=1e-5)
