"""Parans, proof of concept: every rise, set and meridian transit in a window, and the gaps.

For each chosen body and star, every horizon and meridian event at one place within a window,
from Swiss Ephemeris's rise_trans, refined by a second search started 3 minutes before each
first result. Then, for every pair of events of two different points, the time gap and the ARMC
gap. There is no orb and no "paran found" flag: how close counts is the researcher's rule.

This covers the body's actual position (ecliptic latitude included), topocentric, as Swiss
Ephemeris computes it; the zodiacal definition is not covered. Star events are checked against
ERFA's ICRS-to-observed transform, body events against ERFA's horizon transform of Swiss
Ephemeris's positions, both with refraction off.
"""

import math
import shutil
from collections.abc import Callable
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path

import erfa
import pytest
import swisseph

from astro_research.angles import AnglesUndefined
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
from astro_research.parans import (
    PARAN_CONVENTIONS,
    AngularEvent,
    AngularEvents,
    Disc,
    FixedStar,
    HorizonConvention,
    HorizonEvent,
    InvalidHorizonConvention,
    InvalidParanRequest,
    ParanGap,
    Refraction,
    StarNotFound,
    angular_events,
    paran_gaps,
)
from astro_research.positions import Body

PLACES = {
    "london": GeoPosition(latitude=51.5074, longitude=-0.1278),
    "mumbai": GeoPosition(latitude=19.0760, longitude=72.8777),
    "sydney": GeoPosition(latitude=-33.8688, longitude=151.2093),
    "oulu": GeoPosition(latitude=65.0121, longitude=25.4651),
    "tromso": GeoPosition(latitude=69.6492, longitude=18.9553),
}
STARS = {
    "Regulus": FixedStar(designation=",alLeo"),
    "Aldebaran": FixedStar(designation=",alTau"),
    "Spica": FixedStar(designation=",alVir"),
    "Sirius": FixedStar(designation=",alCMa"),
    "Antares": FixedStar(designation=",alSco"),
    "Capella": FixedStar(designation=",alAur"),  # circumpolar at London
    "Canopus": FixedStar(designation=",alCar"),  # never rises at London
    "Polaris": FixedStar(designation=",alUMi"),
}
GEOMETRIC = HorizonConvention(disc=Disc.CENTRE, refraction=None)
ALMANAC = HorizonConvention(
    disc=Disc.UPPER_LIMB, refraction=Refraction(pressure_hpa=1013.25, temperature_c=10.0)
)
# Swiss Ephemeris bisects the rise/set interval 20 times over at most 2 hours (7 ms), and frame
# differences against ERFA are a few mas, under 1 ms at horizon rates (measured: <= 0.0073 s).
EVENT_TOLERANCE_S = 0.01
SECOND = 1 / 86400
SIDEREAL_DAY_S = 86164.0905
MAS = math.pi / 180 / 3.6e6


@pytest.fixture
def eph(fetched_ephe: Path) -> Ephemeris:
    return Ephemeris.open(fetched_ephe)


def utc_time(eph: Ephemeris, utc: datetime) -> AstronomicalTime:
    return eph.time(resolve(UtcTime(utc=utc), PLACES["london"]), TimeScale.CLOCK_AS_UT1)


def day(
    eph: Ephemeris, start: datetime, hours: float = 24.0
) -> tuple[AstronomicalTime, AstronomicalTime]:
    return utc_time(eph, start), utc_time(eph, start + timedelta(hours=hours))


def of(events: AngularEvents, point: Body | FixedStar, event: HorizonEvent) -> list[AngularEvent]:
    return [e for e in events.events if e.point == point and e.event is event]


# --- Inputs ---------------------------------------------------------------------------------


def test_the_horizon_convention_must_be_named(eph: Ephemeris) -> None:
    with pytest.raises(TypeError):
        HorizonConvention(disc=Disc.CENTRE)  # type: ignore[call-arg]
    start, end = day(eph, datetime(2024, 7, 1, tzinfo=UTC))
    with pytest.raises(TypeError):
        angular_events(eph, PLACES["london"], start=start, end=end, bodies=(Body.SUN,), stars=())  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("pressure", "temperature"),
    [(0.0, 10.0), (-5.0, 10.0), (math.nan, 10.0), (1013.25, -300.0), (1013.25, math.inf)],
    ids=["zero-pressure", "negative-pressure", "nan-pressure", "below-absolute-zero", "inf"],
)
def test_refraction_needs_a_positive_pressure_and_a_real_temperature(
    pressure: float, temperature: float
) -> None:
    # Swiss Ephemeris reads a pressure of 0 as "estimate it", which would hide a choice.
    with pytest.raises(InvalidHorizonConvention):
        Refraction(pressure_hpa=pressure, temperature_c=temperature)


@pytest.mark.parametrize("hours", [0.0, -1.0])
def test_the_window_must_end_after_it_starts(eph: Ephemeris, hours: float) -> None:
    start, end = day(eph, datetime(2024, 7, 1, tzinfo=UTC), hours)
    with pytest.raises(InvalidParanRequest, match="window"):
        angular_events(
            eph,
            PLACES["london"],
            start=start,
            end=end,
            bodies=(Body.SUN,),
            stars=(),
            horizon=GEOMETRIC,
        )


def test_points_must_be_distinct(eph: Ephemeris) -> None:
    start, end = day(eph, datetime(2024, 7, 1, tzinfo=UTC))
    with pytest.raises(InvalidParanRequest, match="distinct"):
        angular_events(
            eph,
            PLACES["london"],
            start=start,
            end=end,
            bodies=(Body.SUN, Body.SUN),
            stars=(),
            horizon=GEOMETRIC,
        )
    with pytest.raises(InvalidParanRequest, match="distinct"):
        angular_events(
            eph,
            PLACES["london"],
            start=start,
            end=end,
            bodies=(),
            stars=(STARS["Regulus"], FixedStar(designation=",alLeo")),
            horizon=GEOMETRIC,
        )


def test_an_unknown_star_is_refused(eph: Ephemeris) -> None:
    start, end = day(eph, datetime(2024, 7, 1, tzinfo=UTC))
    with pytest.raises(StarNotFound, match="zzNone"):
        angular_events(
            eph,
            PLACES["london"],
            start=start,
            end=end,
            bodies=(),
            stars=(FixedStar(designation=",zzNone"),),
            horizon=GEOMETRIC,
        )


@pytest.mark.parametrize("latitude", [90.0, -90.0])
def test_the_poles_are_refused(eph: Ephemeris, latitude: float) -> None:
    # The meridian, and with it the ARMC, is undefined there; refused even for a window too
    # short to hold any event.
    pole = GeoPosition(latitude=latitude, longitude=0.0)
    for hours in (24.0, 1 / 60):
        start, end = day(eph, datetime(2024, 7, 1, tzinfo=UTC), hours)
        with pytest.raises(AnglesUndefined):
            angular_events(
                eph, pole, start=start, end=end, bodies=(Body.SUN,), stars=(), horizon=GEOMETRIC
            )


# --- Star events against ERFA ---------------------------------------------------------------


def _catalogue(fetched_ephe: Path) -> dict[str, dict[str, float]]:
    """ICRS data by designation from the pinned sefstars.txt (first record of each)."""
    stars: dict[str, dict[str, float]] = {}
    for line in (fetched_ephe / "sefstars.txt").read_text(encoding="latin-1").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        f = [x.strip() for x in line.split(",")]
        if f[2] != "ICRS" or "," + f[1] in stars:
            continue
        sign = -1.0 if f[6].startswith("-") else 1.0
        stars["," + f[1]] = {
            "ra": (float(f[3]) + float(f[4]) / 60 + float(f[5]) / 3600) * 15,
            "dec": sign * (abs(float(f[6])) + float(f[7]) / 60 + float(f[8]) / 3600),
            "pmra_cos_dec_mas": float(f[9]),
            "pmdec_mas": float(f[10]),
            "rv_kms": float(f[11]),
            "parallax_mas": float(f[12]),
        }
    return stars


def _erfa_observed(
    star: dict[str, float], jd_ut1: float, place: GeoPosition
) -> tuple[float, float]:
    """Observed zenith distance and hour angle (degrees), no refraction, at Swiss Ephemeris's
    UT1 and TT for that instant (fed to ERFA through UTC and dut1)."""
    jd_tt = jd_ut1 + swisseph.deltat_ex(jd_ut1, swisseph.FLG_SWIEPH)[0]
    tai1, tai2 = erfa.tttai(jd_tt, 0.0)
    utc1, utc2 = erfa.taiutc(tai1, tai2)
    dut1 = ((jd_ut1 - utc1) - utc2) * 86400
    dec = math.radians(star["dec"])
    _, zob, hob, *_ = erfa.atco13(
        math.radians(star["ra"]),
        dec,
        star["pmra_cos_dec_mas"] * MAS / math.cos(dec),
        star["pmdec_mas"] * MAS,
        star["parallax_mas"] / 1000,
        star["rv_kms"],
        utc1,
        utc2,
        dut1,
        math.radians(place.longitude),
        math.radians(place.latitude),
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.55,
    )
    return math.degrees(zob), math.degrees(hob)


def _root(f: Callable[[float], float], t0: float) -> float:
    a, b = t0 - 20 * SECOND, t0 + 20 * SECOND
    fa, fb = f(a), f(b)
    for _ in range(60):
        c = b - fb * (b - a) / (fb - fa)
        a, fa, b, fb = b, fb, c, f(c)
        if abs(b - a) < 1e-11:
            break
    return b


def _erfa_offset(
    star: dict[str, float], place: GeoPosition, event: HorizonEvent
) -> Callable[[float], float]:
    """A function of UT1 that is zero at the event: zenith distance minus 90 degrees, or the
    hour angle from 0 or 180 degrees."""
    if event in (HorizonEvent.RISE, HorizonEvent.SET):
        return lambda jd_ut1: _erfa_observed(star, jd_ut1, place)[0] - 90.0
    target = 0.0 if event is HorizonEvent.UPPER_TRANSIT else 180.0
    return lambda jd_ut1: math.remainder(_erfa_observed(star, jd_ut1, place)[1] - target, 360.0)


@pytest.mark.filterwarnings("ignore::erfa.ErfaWarning")  # "dubious year" past the leap-second table
@pytest.mark.parametrize("place_name", ["london", "mumbai", "sydney", "tromso"])
def test_star_events_match_erfa(eph: Ephemeris, fetched_ephe: Path, place_name: str) -> None:
    place, catalogue = PLACES[place_name], _catalogue(fetched_ephe)
    start, end = day(eph, datetime(2024, 7, 1, tzinfo=UTC))
    result = angular_events(
        eph, place, start=start, end=end, bodies=(), stars=tuple(STARS.values()), horizon=GEOMETRIC
    )
    assert result.events
    for e in result.events:
        assert isinstance(e.point, FixedStar)
        t = e.time.jd_ut1
        ref = _root(_erfa_offset(catalogue[e.point.designation], place, e.event), t)
        assert abs(t - ref) * 86400 <= EVENT_TOLERANCE_S, (e.point.designation, e.event)


# --- Body events against ERFA's horizon transform -------------------------------------------


def _topocentric_hour_angle_and_altitude(
    body: Body, jd_ut1: float, place: GeoPosition
) -> tuple[float, float]:
    """From Swiss Ephemeris's topocentric apparent RA/Dec, through ERFA's sidereal time and
    horizon transform (degrees)."""
    swisseph.set_topo(place.longitude, place.latitude, place.elevation_m)
    jd_tt = jd_ut1 + swisseph.deltat_ex(jd_ut1, swisseph.FLG_SWIEPH)[0]
    se_body = {
        Body.SUN: swisseph.SUN,
        Body.MOON: swisseph.MOON,
        Body.MERCURY: swisseph.MERCURY,
        Body.MARS: swisseph.MARS,
        Body.JUPITER: swisseph.JUPITER,
    }[body]
    flags = swisseph.FLG_SWIEPH | swisseph.FLG_EQUATORIAL | swisseph.FLG_TOPOCTR
    (ra, dec, *_), _, _ = swisseph.calc(jd_tt, se_body, flags)
    hour_angle = erfa.gst06a(jd_ut1, 0.0, jd_tt, 0.0) + math.radians(place.longitude - ra)
    _, altitude = erfa.hd2ae(hour_angle, math.radians(dec), math.radians(place.latitude))
    return math.degrees(hour_angle), math.degrees(altitude)


@pytest.mark.parametrize("place_name", ["london", "sydney", "oulu"])
@pytest.mark.parametrize(
    "start", [datetime(2024, 3, 20, tzinfo=UTC), datetime(2024, 12, 15, tzinfo=UTC)]
)
def test_body_events_match_erfas_horizon_transform(
    eph: Ephemeris, place_name: str, start: datetime
) -> None:
    place = PLACES[place_name]
    bodies = (Body.SUN, Body.MOON, Body.MERCURY, Body.MARS, Body.JUPITER)
    window = day(eph, start)
    result = angular_events(
        eph, place, start=window[0], end=window[1], bodies=bodies, stars=(), horizon=GEOMETRIC
    )
    assert result.events
    for e in result.events:
        assert isinstance(e.point, Body)
        t, step = e.time.jd_ut1, 30 * SECOND
        ha, alt = _topocentric_hour_angle_and_altitude(e.point, t, place)
        ha2, alt2 = _topocentric_hour_angle_and_altitude(e.point, t + step, place)
        if e.event in (HorizonEvent.RISE, HorizonEvent.SET):
            offset_s = alt / ((alt2 - alt) / 30)
        else:
            target = 0.0 if e.event is HorizonEvent.UPPER_TRANSIT else 180.0
            offset_s = math.remainder(ha - target, 360.0) / ((ha2 - ha) / 30)
        assert abs(offset_s) <= EVENT_TOLERANCE_S, (e.point, e.event)


# --- Horizon conventions --------------------------------------------------------------------


def _se_bits(horizon: HorizonConvention) -> int:
    bits: int = {
        Disc.UPPER_LIMB: 0,
        Disc.CENTRE: swisseph.BIT_DISC_CENTER,
        Disc.LOWER_LIMB: swisseph.BIT_DISC_BOTTOM,
    }[horizon.disc]
    return bits | (swisseph.BIT_NO_REFRACTION if horizon.refraction is None else 0)


@pytest.mark.parametrize("disc", list(Disc))
@pytest.mark.parametrize(
    "refraction",
    [
        None,
        Refraction(pressure_hpa=1013.25, temperature_c=10.0),
        Refraction(pressure_hpa=850.0, temperature_c=-20.0),
    ],
)
def test_rise_and_set_follow_the_chosen_convention(
    eph: Ephemeris, disc: Disc, refraction: Refraction | None
) -> None:
    # Swiss Ephemeris's refraction and disc model is the definition; the convention must reach
    # it unchanged. Its result depends on where its search starts at the bisection's resolution
    # (7 ms), so the comparison allows the event tolerance.
    horizon = HorizonConvention(disc=disc, refraction=refraction)
    place = PLACES["london"]
    start, end = day(eph, datetime(2024, 12, 15, tzinfo=UTC))
    result = angular_events(
        eph, place, start=start, end=end, bodies=(Body.SUN, Body.MOON), stars=(), horizon=horizon
    )
    pressure, temperature = (
        (0.0, 0.0) if refraction is None else (refraction.pressure_hpa, refraction.temperature_c)
    )
    geopos = (place.longitude, place.latitude, place.elevation_m)
    for e in result.events:
        if e.event not in (HorizonEvent.RISE, HorizonEvent.SET):
            continue
        assert isinstance(e.point, Body)
        rsmi = (
            swisseph.CALC_RISE if e.event is HorizonEvent.RISE else swisseph.CALC_SET
        ) | _se_bits(horizon)
        body = swisseph.SUN if e.point is Body.SUN else swisseph.MOON
        first = swisseph.rise_trans(
            e.time.jd_ut1 - 3 / 1440, body, rsmi, geopos, pressure, temperature, swisseph.FLG_SWIEPH
        )[1][0]
        assert e.time.jd_ut1 == pytest.approx(first, abs=EVENT_TOLERANCE_S * SECOND)


def test_the_conventions_move_sunrise_the_expected_way(eph: Ephemeris) -> None:
    # The upper limb clears the horizon before the centre, the centre before the lower limb, and
    # refraction lifts all of them earlier.
    start, end = day(eph, datetime(2024, 12, 15, tzinfo=UTC))
    rises = {}
    for disc in Disc:
        for refraction in (None, Refraction(pressure_hpa=1013.25, temperature_c=10.0)):
            result = angular_events(
                eph,
                PLACES["london"],
                start=start,
                end=end,
                bodies=(Body.SUN,),
                stars=(),
                horizon=HorizonConvention(disc=disc, refraction=refraction),
            )
            (rise,) = of(result, Body.SUN, HorizonEvent.RISE)
            rises[(disc, refraction is not None)] = rise.time.jd_tt
    for refracted in (False, True):
        assert (
            rises[(Disc.UPPER_LIMB, refracted)]
            < rises[(Disc.CENTRE, refracted)]
            < rises[(Disc.LOWER_LIMB, refracted)]
        )
    for disc in Disc:
        assert rises[(disc, True)] < rises[(disc, False)]


# --- The window -----------------------------------------------------------------------------


def test_events_lie_in_the_window_in_time_order(eph: Ephemeris) -> None:
    start, end = day(eph, datetime(2024, 7, 1, tzinfo=UTC))
    result = angular_events(
        eph,
        PLACES["london"],
        start=start,
        end=end,
        bodies=(Body.SUN, Body.MOON, Body.MARS),
        stars=(STARS["Regulus"], STARS["Spica"]),
        horizon=ALMANAC,
    )
    times = [e.time.jd_tt for e in result.events]
    assert times == sorted(times)
    assert all(start.jd_tt <= t < end.jd_tt for t in times)
    # A body or star that rises and sets has each event once in a day, or twice when the window
    # catches the start of a second cycle.
    for point in (Body.SUN, Body.MARS, STARS["Regulus"], STARS["Spica"]):
        for event in HorizonEvent:
            assert 1 <= len(of(result, point, event)) <= 2, (point, event)


def test_a_star_can_rise_twice_in_one_day(eph: Ephemeris) -> None:
    place, regulus = PLACES["london"], STARS["Regulus"]
    start, end = day(eph, datetime(2024, 7, 1, tzinfo=UTC))
    first = of(
        angular_events(
            eph, place, start=start, end=end, bodies=(), stars=(regulus,), horizon=GEOMETRIC
        ),
        regulus,
        HorizonEvent.RISE,
    )[0]
    # A 24-hour window starting 2 minutes before that rise holds the next one, a sidereal day on.
    window_start = eph.time_from_tt(first.time.jd_tt - 120 * SECOND, TimeScale.CLOCK_AS_UT1)
    window_end = eph.time_from_tt(window_start.jd_tt + 1.0, TimeScale.CLOCK_AS_UT1)
    rises = of(
        angular_events(
            eph,
            place,
            start=window_start,
            end=window_end,
            bodies=(),
            stars=(regulus,),
            horizon=GEOMETRIC,
        ),
        regulus,
        HorizonEvent.RISE,
    )
    assert len(rises) == 2
    # The star's apparent RA moves by under 1 arcsec a day (precession, nutation, aberration),
    # under 0.07 s of rotation.
    gap_s = (rises[1].time.jd_tt - rises[0].time.jd_tt) * 86400
    assert gap_s == pytest.approx(SIDEREAL_DAY_S, abs=0.1)


def test_a_day_without_moonrise_has_none(eph: Ephemeris) -> None:
    # London, UT day 2024-01-03: the Moon rises at 23:02 on the 2nd and next at 00:12 on the 4th,
    # and sets at 11:27 on the 3rd (measured). Absent from this window is not the same as never
    # rising.
    start, end = day(eph, datetime(2024, 1, 3, tzinfo=UTC))
    result = angular_events(
        eph, PLACES["london"], start=start, end=end, bodies=(Body.MOON,), stars=(), horizon=ALMANAC
    )
    assert of(result, Body.MOON, HorizonEvent.RISE) == []
    assert len(of(result, Body.MOON, HorizonEvent.SET)) == 1
    assert result.no_rise_or_set == ()


def test_circumpolar_and_never_rising_stars_still_transit(eph: Ephemeris) -> None:
    capella, canopus = STARS["Capella"], STARS["Canopus"]
    start, end = day(eph, datetime(2024, 7, 1, tzinfo=UTC))
    result = angular_events(
        eph,
        PLACES["london"],
        start=start,
        end=end,
        bodies=(),
        stars=(capella, canopus),
        horizon=ALMANAC,
    )
    # Swiss Ephemeris's -2 cannot tell the two cases apart, so neither does the result.
    assert set(result.no_rise_or_set) == {
        (capella, HorizonEvent.RISE),
        (capella, HorizonEvent.SET),
        (canopus, HorizonEvent.RISE),
        (canopus, HorizonEvent.SET),
    }
    for star in (capella, canopus):
        assert of(result, star, HorizonEvent.RISE) == of(result, star, HorizonEvent.SET) == []
        assert of(result, star, HorizonEvent.UPPER_TRANSIT)
        assert of(result, star, HorizonEvent.LOWER_TRANSIT)


def test_the_moon_rises_again_after_days_of_not_rising(eph: Ephemeris) -> None:
    # Tromso, January 2024 (near the major lunar standstill): the Moon rises up to the 6th,
    # never rises from the 7th to the 12th (declination down to -28.1 degrees), and rises again
    # from the 13th (measured with Swiss Ephemeris, day by day).
    start, end = day(eph, datetime(2024, 1, 5, tzinfo=UTC), 11 * 24.0)
    result = angular_events(
        eph, PLACES["tromso"], start=start, end=end, bodies=(Body.MOON,), stars=(), horizon=ALMANAC
    )
    rises = [e.time.instant.utc for e in of(result, Body.MOON, HorizonEvent.RISE)]
    assert (Body.MOON, HorizonEvent.RISE) in result.no_rise_or_set
    assert any(utc < datetime(2024, 1, 7, tzinfo=UTC) for utc in rises)
    assert not any(
        datetime(2024, 1, 8, tzinfo=UTC) <= utc < datetime(2024, 1, 12, tzinfo=UTC) for utc in rises
    )
    assert any(utc >= datetime(2024, 1, 13, tzinfo=UTC) for utc in rises)


def test_star_names_are_resolved_from_the_catalogue(eph: Ephemeris) -> None:
    start, end = day(eph, datetime(2024, 7, 1, tzinfo=UTC))
    result = angular_events(
        eph,
        PLACES["london"],
        start=start,
        end=end,
        bodies=(),
        stars=(STARS["Regulus"], STARS["Aldebaran"]),
        horizon=GEOMETRIC,
    )
    assert dict(result.star_names) == {
        STARS["Regulus"]: "Regulus,alLeo",
        STARS["Aldebaran"]: "Aldebaran,alTau",
    }


def test_each_event_carries_the_armc_at_its_instant(eph: Ephemeris) -> None:
    place = PLACES["sydney"]
    start, end = day(eph, datetime(2024, 7, 1, tzinfo=UTC))
    result = angular_events(
        eph,
        place,
        start=start,
        end=end,
        bodies=(Body.SUN, Body.MOON),
        stars=(STARS["Sirius"],),
        horizon=ALMANAC,
    )
    for e in result.events:
        assert e.armc == eph.angles(e.time, place).armc


def test_the_result_records_its_inputs_and_conventions(eph: Ephemeris) -> None:
    place = PLACES["mumbai"]
    start, end = day(eph, datetime(2024, 7, 1, tzinfo=UTC))
    result = angular_events(
        eph,
        place,
        start=start,
        end=end,
        bodies=(Body.SUN,),
        stars=(STARS["Spica"],),
        horizon=ALMANAC,
    )
    assert (result.place, result.start, result.end, result.horizon) == (place, start, end, ALMANAC)
    assert result.bodies == (Body.SUN,)
    assert result.stars == (STARS["Spica"],)
    assert result.ephemeris == eph.provenance
    assert result.conventions == PARAN_CONVENTIONS
    assert PARAN_CONVENTIONS.startswith("parans-v1:")
    assert all(e.time.time_scale is start.time_scale for e in result.events)


# --- Gaps -----------------------------------------------------------------------------------


def test_gaps_pair_every_two_events_of_different_points(eph: Ephemeris) -> None:
    start, end = day(eph, datetime(2024, 7, 1, tzinfo=UTC))
    result = angular_events(
        eph,
        PLACES["london"],
        start=start,
        end=end,
        bodies=(Body.SUN, Body.MOON),
        stars=(STARS["Regulus"], STARS["Capella"]),
        horizon=ALMANAC,
    )
    gaps = paran_gaps(result)
    counts: dict[object, int] = {}
    for e in result.events:
        counts[e.point] = counts.get(e.point, 0) + 1
    points = list(counts)
    expected = sum(counts[a] * counts[b] for i, a in enumerate(points) for b in points[i + 1 :])
    assert len(gaps) == expected
    for gap in gaps:
        assert gap.first.point != gap.second.point
        assert gap.first.time.jd_tt <= gap.second.time.jd_tt
        assert gap.seconds == (gap.second.time.jd_tt - gap.first.time.jd_tt) * 86400
        difference = gap.second.armc - gap.first.armc
        wrapped = (
            difference - 360.0
            if difference > 180.0
            else difference + 360.0
            if difference <= -180.0
            else difference
        )
        assert gap.armc == wrapped


def test_a_gap_is_only_numbers(eph: Ephemeris) -> None:
    # No orb, no flag: deciding which gaps count as parans is the researcher's rule.
    assert [f.name for f in fields(ParanGap)] == ["first", "second", "seconds", "armc"]


# --- Refusals -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "start", [datetime(1799, 12, 29, tzinfo=UTC), datetime(2400, 1, 10, 12, tzinfo=UTC)]
)
def test_windows_outside_the_files_are_refused(eph: Ephemeris, start: datetime) -> None:
    # Swiss Ephemeris would fall back to its Moshier model without saying so (observed).
    window = day(eph, start)
    for bodies, stars in (((Body.SUN,), ()), ((), (STARS["Regulus"],))):
        with pytest.raises(DateOutsideEphemeris):
            angular_events(
                eph,
                PLACES["london"],
                start=window[0],
                end=window[1],
                bodies=bodies,
                stars=stars,
                horizon=GEOMETRIC,
            )


def test_events_refuse_after_the_data_path_moves(
    eph: Ephemeris, fetched_ephe: Path, tmp_path: Path
) -> None:
    start, end = day(eph, datetime(2024, 7, 1, tzinfo=UTC))
    other = tmp_path / "ephe"
    other.mkdir()
    for entry in load_manifest().files:
        shutil.copy2(fetched_ephe / entry.name, other / entry.name)
    verify_ephemeris(other)
    with pytest.raises(StaleEphemeris):
        angular_events(
            eph,
            PLACES["london"],
            start=start,
            end=end,
            bodies=(Body.SUN,),
            stars=(),
            horizon=GEOMETRIC,
        )
    Ephemeris.open(fetched_ephe)  # restore the process-wide path for later tests
