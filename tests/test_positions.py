"""Positions: apparent geocentric Sun, Moon and Mercury-Pluto through the verified handle.

Independent validation of the values themselves is layers 2 and 3 (ERFA + Horizons). These tests
pin the conventions, units and failure behaviour, using physical bounds and well-known events
where an outside reference is needed.
"""

import math
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import swisseph

from astro_research.astronomical_time import AstronomicalTime, TimeScale
from astro_research.civil_time import UtcTime, resolve
from astro_research.ephemeris import (
    DateOutsideEphemeris,
    Ephemeris,
    StaleEphemeris,
    load_manifest,
    verify_ephemeris,
)
from astro_research.geo import GeoPosition
from astro_research.positions import POSITION_CONVENTIONS, Body, Positions

GREENWICH = GeoPosition(latitude=51.4769, longitude=0.0)


@pytest.fixture
def eph(fetched_ephe: Path) -> Ephemeris:
    return Ephemeris.open(fetched_ephe)


def at(
    eph: Ephemeris, year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0
) -> AstronomicalTime:
    utc = datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    return eph.time(resolve(UtcTime(utc=utc), GREENWICH), TimeScale.CLOCK_AS_UT1)


# --- Shape --------------------------------------------------------------------------------


def test_all_ten_bodies_by_default_in_order(eph: Ephemeris) -> None:
    positions = eph.positions(at(eph, 2000, 1, 1, 12))
    assert [p.body for p in positions.bodies] == list(Body)


def test_values_are_in_range_and_finite(eph: Ephemeris) -> None:
    for p in eph.positions(at(eph, 2000, 1, 1, 12)).bodies:
        assert 0 <= p.longitude < 360 and 0 <= p.right_ascension < 360
        assert -90 <= p.latitude <= 90 and -90 <= p.declination <= 90
        assert p.distance_au > 0
        speeds = (p.longitude_speed, p.latitude_speed, p.distance_speed)
        assert all(math.isfinite(v) for v in speeds)


def test_subset_keeps_order_and_drops_duplicates(eph: Ephemeris) -> None:
    positions = eph.positions(at(eph, 2000, 1, 1, 12), [Body.MARS, Body.SUN, Body.MARS])
    assert [p.body for p in positions.bodies] == [Body.MARS, Body.SUN]
    assert positions[Body.SUN].body is Body.SUN
    with pytest.raises(KeyError):
        positions[Body.MOON]


def test_result_records_time_ephemeris_and_conventions(eph: Ephemeris) -> None:
    time = at(eph, 2000, 1, 1, 12)
    positions = eph.positions(time)
    assert isinstance(positions, Positions)
    assert positions.time is time
    assert positions.ephemeris == eph.provenance
    assert positions.conventions == POSITION_CONVENTIONS


# --- Conventions --------------------------------------------------------------------------


@pytest.mark.parametrize("body", list(Body))
def test_values_are_swiss_ephemeris_defaults_unrounded(eph: Ephemeris, body: Body) -> None:
    # Swiss Ephemeris's default output is the convention we adopt: geocentric, apparent, true
    # ecliptic and equinox of date, Jupiter-Pluto as system barycenters. Equality is exact: the
    # engine must not round or convert.
    time = at(eph, 1987, 6, 15, 3, 25, 41)
    p = eph.positions(time, [body])[body]
    se_body = getattr(swisseph, body.name)
    ecl, _, _ = swisseph.calc(time.jd_tt, se_body, swisseph.FLG_SWIEPH | swisseph.FLG_SPEED)
    equ, _, _ = swisseph.calc(time.jd_tt, se_body, swisseph.FLG_SWIEPH | swisseph.FLG_EQUATORIAL)
    assert (p.longitude, p.latitude, p.distance_au) == tuple(ecl[:3])
    assert (p.longitude_speed, p.latitude_speed, p.distance_speed) == tuple(ecl[3:])
    assert (p.right_ascension, p.declination) == tuple(equ[:2])


def test_equatorial_and_ecliptic_describe_the_same_direction(eph: Ephemeris) -> None:
    # Rotating the ecliptic position by the true obliquity of date must give the RA/Dec.
    time = at(eph, 2024, 7, 1, 12)
    true_obliquity = swisseph.calc(time.jd_tt, swisseph.ECL_NUT, 0)[0][0]
    for p in eph.positions(time).bodies:
        ra, dec, _ = swisseph.cotrans((p.longitude, p.latitude, 1.0), -true_obliquity)
        assert ra == pytest.approx(p.right_ascension, abs=1e-9)
        assert dec == pytest.approx(p.declination, abs=1e-9)


# --- Speeds: units and sign ---------------------------------------------------------------


def test_daily_motion_of_sun_and_moon_is_physical(eph: Ephemeris) -> None:
    # Earth's orbital eccentricity bounds the Sun's motion to ~0.953-1.020 deg/day; the Moon's
    # ranges ~11.7-15.4 deg/day. Units are degrees per day.
    for month in range(1, 13):
        p = eph.positions(at(eph, 2024, month, 15), [Body.SUN, Body.MOON])
        assert 0.95 < p[Body.SUN].longitude_speed < 1.03
        assert 11.5 < p[Body.MOON].longitude_speed < 15.6


def test_mercury_retrograde_april_2024(eph: Ephemeris) -> None:
    # Mercury was retrograde from 1 to 25 April 2024 (the week of the 8 April solar eclipse).
    assert eph.positions(at(eph, 2024, 4, 10), [Body.MERCURY])[Body.MERCURY].longitude_speed < 0
    assert eph.positions(at(eph, 2024, 5, 10), [Body.MERCURY])[Body.MERCURY].longitude_speed > 0


@pytest.mark.parametrize("body", list(Body))
def test_speed_matches_the_change_in_position(eph: Ephemeris, body: Body) -> None:
    # Central difference over +-1 hour; the Moon's speed itself changes by <0.1%/hour.
    before = eph.positions(at(eph, 2000, 1, 1, 11), [body])[body]
    now = eph.positions(at(eph, 2000, 1, 1, 12), [body])[body]
    after = eph.positions(at(eph, 2000, 1, 1, 13), [body])[body]
    change = ((after.longitude - before.longitude + 180) % 360 - 180) / (2 / 24)
    assert change == pytest.approx(now.longitude_speed, rel=1e-3, abs=1e-6)


# --- Failure behaviour --------------------------------------------------------------------


def test_dates_outside_the_pinned_files_are_refused(eph: Ephemeris) -> None:
    # 1750 needs sepl_12.se1, which the manifest does not include. Swiss Ephemeris would
    # silently switch to its Moshier model; the engine refuses instead.
    time = at(eph, 1750, 6, 1, 12)
    with pytest.raises(DateOutsideEphemeris, match="sepl_12"):
        eph.positions(time, [Body.SUN])


def test_positions_refuse_after_the_data_path_moves(
    eph: Ephemeris, fetched_ephe: Path, tmp_path: Path
) -> None:
    time = at(eph, 2000, 1, 1, 12)
    other = tmp_path / "ephe"
    other.mkdir()
    for entry in load_manifest().files:
        shutil.copy2(fetched_ephe / entry.name, other / entry.name)
    verify_ephemeris(other)
    with pytest.raises(StaleEphemeris):
        eph.positions(time)
    Ephemeris.open(fetched_ephe)  # restore the process-wide path for later tests
