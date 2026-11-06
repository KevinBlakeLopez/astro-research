"""Angles: Ascendant, MC, Descendant, IC, Vertex and ARMC through the verified handle.

The definition checks are independent of Swiss Ephemeris's angle code: from the engine's ARMC and
the true obliquity, spherical trigonometry here computes where each point lies on the sky. The
sidereal time itself is checked against ERFA's IAU standard where Swiss Ephemeris uses the IERS
model (1850-2050), and its documented switch to a long-term model outside that window is guarded.

Conventions (Swiss Ephemeris manual §6.3-6.4): the Ascendant is always the eastern rising point
of the ecliptic, accepting an MC below the horizon beyond the polar circles; the Vertex is the
ecliptic point due west and is always in the western hemisphere (on the equator, 0 Aries or 0
Libra). Angles follow the Earth's rotation, so they are computed from UT1.
"""

import math
import shutil
from datetime import UTC, datetime
from pathlib import Path

import erfa
import pytest
import swisseph

from astro_research.angles import ANGLE_CONVENTIONS, Angles, AnglesUndefined
from astro_research.astronomical_time import AstronomicalTime, TimeScale
from astro_research.civil_time import UtcTime, resolve
from astro_research.ephemeris import Ephemeris, StaleEphemeris, load_manifest, verify_ephemeris
from astro_research.geo import GeoPosition

PLACES = {
    "equator": GeoPosition(latitude=0.0, longitude=30.0),
    "mumbai": GeoPosition(latitude=19.0760, longitude=72.8777),
    "london": GeoPosition(latitude=51.5074, longitude=-0.1278),
    "sydney": GeoPosition(latitude=-33.8688, longitude=151.2093),
    "oulu": GeoPosition(latitude=65.0121, longitude=25.4651),
    "tromso": GeoPosition(latitude=69.6492, longitude=18.9553),
    "longyearbyen": GeoPosition(latitude=78.2232, longitude=15.6267),
    "antarctica": GeoPosition(latitude=-70.0, longitude=0.0),
}
INSTANTS = {
    "1850-06-01": datetime(1850, 6, 1, 12, 0, tzinfo=UTC),
    "1900-03-15": datetime(1900, 3, 15, 6, 30, tzinfo=UTC),
    "1987-06-15": datetime(1987, 6, 15, 3, 25, 41, tzinfo=UTC),
    "2000-01-01": datetime(2000, 1, 1, 12, 0, tzinfo=UTC),
    "2024-07-01": datetime(2024, 7, 1, 18, 45, tzinfo=UTC),
}
CASES = [
    pytest.param(place, utc, id=f"{place_name}-{utc_name}")
    for place_name, place in PLACES.items()
    for utc_name, utc in INSTANTS.items()
]
# The Vertex is degenerate on the equator (an equinox point); that case has its own test.
OFF_EQUATOR = [
    pytest.param(place, utc, id=f"{place_name}-{utc_name}")
    for place_name, place in PLACES.items()
    if place.latitude != 0
    for utc_name, utc in INSTANTS.items()
]
# Float64 trigonometry on SE's own ARMC and obliquity; 1e-9 degree is 3.6 micro-arcseconds.
DEFINITION_TOLERANCE_DEG = 1e-9
MAS_PER_DEGREE = 3.6e6
SIDEREAL_ROTATION_DEG_PER_DAY = 360.98564736629  # Earth rotation angle rate (IERS 2010)


@pytest.fixture
def eph(fetched_ephe: Path) -> Ephemeris:
    return Ephemeris.open(fetched_ephe)


def at(
    eph: Ephemeris, utc: datetime, scale: TimeScale = TimeScale.CLOCK_AS_UT1
) -> AstronomicalTime:
    return eph.time(resolve(UtcTime(utc=utc), GeoPosition(latitude=0.0, longitude=0.0)), scale)


def true_obliquity(time: AstronomicalTime) -> float:
    return float(swisseph.calc(time.jd_tt, swisseph.ECL_NUT, 0)[0][0])


def wrap(deg: float) -> float:
    return (deg + 180) % 360 - 180


def horizon(longitude: float, angles: Angles, obliquity: float) -> tuple[float, float, float]:
    """(hour angle, north component, east component) of the ecliptic point at `longitude`."""
    lam, eps, lat = (math.radians(v) for v in (longitude, obliquity, angles.place.latitude))
    ra = math.atan2(math.sin(lam) * math.cos(eps), math.cos(lam))
    dec = math.asin(math.sin(lam) * math.sin(eps))
    hour_angle = math.radians(angles.armc) - ra
    north = math.sin(dec) * math.cos(lat) - math.cos(dec) * math.cos(hour_angle) * math.sin(lat)
    east = -math.cos(dec) * math.sin(hour_angle)
    return math.degrees(hour_angle), north, east


def altitude(longitude: float, angles: Angles, obliquity: float) -> float:
    lam, eps, lat = (math.radians(v) for v in (longitude, obliquity, angles.place.latitude))
    ra = math.atan2(math.sin(lam) * math.cos(eps), math.cos(lam))
    dec = math.asin(math.sin(lam) * math.sin(eps))
    hour_angle = math.radians(angles.armc) - ra
    sin_alt = math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(hour_angle)
    return math.degrees(math.asin(sin_alt))


# --- Definitions --------------------------------------------------------------------------


@pytest.mark.parametrize(("place", "utc"), CASES)
def test_ascendant_is_on_the_eastern_horizon(
    eph: Ephemeris, place: GeoPosition, utc: datetime
) -> None:
    time = at(eph, utc)
    angles = eph.angles(time, place)
    eps = true_obliquity(time)
    assert altitude(angles.ascendant, angles, eps) == pytest.approx(0, abs=DEFINITION_TOLERANCE_DEG)
    _, _, east = horizon(angles.ascendant, angles, eps)
    assert east > 0


@pytest.mark.parametrize(("place", "utc"), CASES)
def test_midheaven_is_on_the_upper_meridian(
    eph: Ephemeris, place: GeoPosition, utc: datetime
) -> None:
    time = at(eph, utc)
    angles = eph.angles(time, place)
    hour_angle, _, _ = horizon(angles.midheaven, angles, true_obliquity(time))
    assert wrap(hour_angle) == pytest.approx(0, abs=DEFINITION_TOLERANCE_DEG)


@pytest.mark.parametrize(("place", "utc"), OFF_EQUATOR)
def test_vertex_is_due_west(eph: Ephemeris, place: GeoPosition, utc: datetime) -> None:
    time = at(eph, utc)
    angles = eph.angles(time, place)
    _, north, east = horizon(angles.vertex, angles, true_obliquity(time))
    assert north == pytest.approx(0, abs=math.radians(DEFINITION_TOLERANCE_DEG))
    assert east < 0


@pytest.mark.parametrize(("place", "utc"), CASES)
def test_descendant_and_ic_are_derived_opposites(
    eph: Ephemeris, place: GeoPosition, utc: datetime
) -> None:
    angles = eph.angles(at(eph, utc), place)
    assert angles.descendant == (angles.ascendant + 180) % 360
    assert angles.imum_coeli == (angles.midheaven + 180) % 360


# --- Conventions at the edges -------------------------------------------------------------


def test_vertex_on_the_equator_is_an_equinox_point(eph: Ephemeris) -> None:
    for utc in INSTANTS.values():
        angles = eph.angles(at(eph, utc), PLACES["equator"])
        assert min(abs(wrap(angles.vertex - 0)), abs(wrap(angles.vertex - 180))) < 1e-9
        assert angles.vertex_speed == pytest.approx(0, abs=1e-6)


def test_beyond_polar_circle_flag(eph: Ephemeris) -> None:
    time = at(eph, INSTANTS["2000-01-01"])
    eps = true_obliquity(time)
    for name, place in PLACES.items():
        expected = abs(place.latitude) > 90 - eps
        assert eph.angles(time, place).beyond_polar_circle is expected, name


def test_beyond_polar_circle_uses_the_true_obliquity(eph: Ephemeris) -> None:
    # Nutation moves the polar circle by up to ~9 arcsec; a place between the true and the
    # mean boundary tells the two apart.
    time = at(eph, INSTANTS["2000-01-01"])
    true_eps, mean_eps = swisseph.calc(time.jd_tt, swisseph.ECL_NUT, 0)[0][:2]
    between = GeoPosition(latitude=90 - (true_eps + mean_eps) / 2, longitude=0.0)
    assert eph.angles(time, between).beyond_polar_circle is (true_eps > mean_eps)


def test_beyond_polar_circle_the_mc_may_be_below_the_horizon(eph: Ephemeris) -> None:
    # SE keeps the Ascendant as the eastern rising point and accepts an MC below the horizon.
    seen_below = False
    for hour in range(24):
        time = at(eph, datetime(2000, 1, 1, hour, tzinfo=UTC))
        angles = eph.angles(time, PLACES["longyearbyen"])
        eps = true_obliquity(time)
        hour_angle, _, _ = horizon(angles.midheaven, angles, eps)
        assert wrap(hour_angle) == pytest.approx(0, abs=DEFINITION_TOLERANCE_DEG)
        seen_below |= altitude(angles.midheaven, angles, eps) < 0
    assert seen_below


@pytest.mark.parametrize("latitude", [90.0, -90.0])
def test_angles_are_refused_at_the_poles(eph: Ephemeris, latitude: float) -> None:
    with pytest.raises(AnglesUndefined):
        eph.angles(at(eph, INSTANTS["2000-01-01"]), GeoPosition(latitude=latitude, longitude=0.0))


# --- Speeds -------------------------------------------------------------------------------


def test_armc_turns_at_the_earth_rotation_rate(eph: Ephemeris) -> None:
    angles = eph.angles(at(eph, INSTANTS["2000-01-01"]), PLACES["london"])
    assert angles.armc_speed == pytest.approx(SIDEREAL_ROTATION_DEG_PER_DAY, abs=1e-4)


@pytest.mark.parametrize("place_name", ["mumbai", "london", "sydney", "oulu"])
def test_speeds_match_the_change_in_each_angle(eph: Ephemeris, place_name: str) -> None:
    place, utc = PLACES[place_name], INSTANTS["1987-06-15"]
    step_s = 2.0
    before = eph.angles(at(eph, utc.replace(second=utc.second - 2)), place)
    now = eph.angles(at(eph, utc), place)
    after = eph.angles(at(eph, utc.replace(second=utc.second + 2)), place)
    for field in ("armc", "ascendant", "midheaven", "vertex"):
        change = wrap(getattr(after, field) - getattr(before, field)) / (2 * step_s / 86400)
        assert change == pytest.approx(getattr(now, f"{field}_speed"), rel=1e-4), field


# --- Sidereal time ------------------------------------------------------------------------


@pytest.mark.parametrize("year", [1850, 1900, 1950, 2000, 2024, 2049])
@pytest.mark.parametrize("place_name", ["london", "sydney", "longyearbyen"])
def test_armc_matches_iau_sidereal_time_between_1850_and_2050(
    eph: Ephemeris, year: int, place_name: str
) -> None:
    # Inside this window Swiss Ephemeris uses the IERS 2010 model. The allowance is layer 2's
    # nutation allowance (1 mas): the equation of the equinoxes is built from the nutation.
    place = PLACES[place_name]
    time = at(eph, datetime(year, 6, 1, 12, tzinfo=UTC))
    gast = math.degrees(
        erfa.gst06a(2400000.5, time.jd_ut1 - 2400000.5, 2400000.5, time.jd_tt - 2400000.5)
    )
    armc = eph.angles(time, place).armc
    assert abs(wrap(armc - gast - place.longitude)) * MAS_PER_DEGREE <= 1.0


@pytest.mark.parametrize(
    ("switch", "minimum_step_mas"),
    [(datetime(1850, 1, 1, tzinfo=UTC), 50.0), (datetime(2050, 1, 1, tzinfo=UTC), 1000.0)],
)
def test_sidereal_model_switches_at_the_documented_dates(
    eph: Ephemeris, switch: datetime, minimum_step_mas: float
) -> None:
    # Outside 1850-2050 Swiss Ephemeris uses its long-term sidereal model (swephlib.c:3459-3481).
    # Observed steps: 97.3 mas at 1850-01-01 and 1907.7 mas at 2050-01-01. The guard thresholds
    # are far above the 1 mas agreement inside the window, so a model change fails here.
    def armc_minus_rotation(offset_s: float) -> float:
        time = at(eph, datetime.fromtimestamp(switch.timestamp() + offset_s, tz=UTC))
        armc = eph.angles(time, PLACES["london"]).armc
        return armc - SIDEREAL_ROTATION_DEG_PER_DAY * offset_s / 86400

    step = wrap(armc_minus_rotation(0.001) - armc_minus_rotation(-0.001)) * MAS_PER_DEGREE
    assert abs(step) >= minimum_step_mas


# --- Time scale, records and failure behaviour --------------------------------------------


def test_angles_follow_ut1(eph: Ephemeris) -> None:
    # The two time-scale conventions differ by UT1 - UTC after 1972; ARMC moves with it.
    clock = at(eph, INSTANTS["2000-01-01"], TimeScale.CLOCK_AS_UT1)
    ut1 = at(eph, INSTANTS["2000-01-01"], TimeScale.UT1_FROM_DELTA_T)
    shift = wrap(eph.angles(ut1, PLACES["london"]).armc - eph.angles(clock, PLACES["london"]).armc)
    expected = SIDEREAL_ROTATION_DEG_PER_DAY * ut1.ut1_minus_utc_s / 86400
    assert shift == pytest.approx(expected, abs=1e-8)


def test_values_are_swiss_ephemeris_output_unrounded(eph: Ephemeris) -> None:
    time = at(eph, INSTANTS["1987-06-15"])
    place = PLACES["london"]
    angles = eph.angles(time, place)
    _, ascmc, _, speeds = swisseph.houses_ex2(
        time.jd_ut1, place.latitude, place.longitude, b"E", swisseph.FLG_SWIEPH
    )
    assert (angles.ascendant, angles.midheaven, angles.armc, angles.vertex) == (
        ascmc[0],
        ascmc[1],
        ascmc[2],
        ascmc[3],
    )
    assert (angles.ascendant_speed, angles.midheaven_speed) == (speeds[0], speeds[1])
    assert (angles.armc_speed, angles.vertex_speed) == (speeds[2], speeds[3])


def test_result_records_its_inputs_and_conventions(eph: Ephemeris) -> None:
    time = at(eph, INSTANTS["2000-01-01"])
    angles = eph.angles(time, PLACES["london"])
    assert angles.time is time
    assert angles.place is PLACES["london"]
    assert angles.ephemeris == eph.provenance
    assert angles.conventions == ANGLE_CONVENTIONS
    assert angles.true_obliquity == swisseph.calc(time.jd_tt, swisseph.ECL_NUT, 0)[0][0]


def test_angles_refuse_after_the_data_path_moves(
    eph: Ephemeris, fetched_ephe: Path, tmp_path: Path
) -> None:
    time = at(eph, INSTANTS["2000-01-01"])
    other = tmp_path / "ephe"
    other.mkdir()
    for entry in load_manifest().files:
        shutil.copy2(fetched_ephe / entry.name, other / entry.name)
    verify_ephemeris(other)
    with pytest.raises(StaleEphemeris):
        eph.angles(time, PLACES["london"])
    Ephemeris.open(fetched_ephe)  # restore the process-wide path for later tests
