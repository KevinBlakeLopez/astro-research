"""Eclipses: the nearest solar and lunar eclipses before and after a birth.

Swiss Ephemeris finds and classifies each eclipse; its instant of greatest eclipse is then
refined to NASA's definition (solar: the shadow axis passes closest to Earth's centre; lunar:
the Moon is closest, in geocentric angle, to the anti-Sun point), using Swiss Ephemeris's
apparent positions. Each eclipse also carries the exact syzygy, and the Sun's and Moon's
positions at both instants.

The reference is NASA's Five Millennium Canon for 1901-2100 (tests/fixtures/
nasa_eclipse_canon.json; "Eclipse Predictions by Fred Espenak and Jean Meeus (NASA's GSFC)").
Which eclipse, its type and its saros series must agree exactly. Times and magnitudes are held
to the largest differences observed, rounded up: those bounds are measured, not derived. The
refined instants are also checked against their definitions directly.
"""

import json
import math
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
import swisseph

from astro_research.astronomical_time import AstronomicalTime, InstantInLeapSecond, TimeScale
from astro_research.civil_time import UtcTime, resolve
from astro_research.eclipses import (
    ECLIPSE_CONVENTIONS,
    LUNAR_TYPES,
    SOLAR_TYPES,
    Eclipse,
    EclipseKind,
    EclipseType,
    InvalidEclipseTypes,
    nearest_eclipses,
    next_eclipse,
    previous_eclipse,
)
from astro_research.ephemeris import (
    DateOutsideEphemeris,
    DateOutsideUt1Data,
    Ephemeris,
    StaleEphemeris,
    load_manifest,
    verify_ephemeris,
)
from astro_research.geo import GeoPosition
from astro_research.positions import Body

CANON = json.loads((Path(__file__).parent / "fixtures" / "nasa_eclipse_canon.json").read_text())
LONDON = GeoPosition(latitude=51.5074, longitude=-0.1278)
SECOND = 1 / 86400
LETTERS = {
    "solar": {
        "T": EclipseType.TOTAL,
        "A": EclipseType.ANNULAR,
        "H": EclipseType.HYBRID,
        "P": EclipseType.PARTIAL,
    },
    "lunar": {"T": EclipseType.TOTAL, "P": EclipseType.PARTIAL, "N": EclipseType.PENUMBRAL},
}
# NASA calls these annular; Swiss Ephemeris calls them annular-total (hybrid). Both are central.
TYPE_EXCEPTIONS = {"1927-01-03": EclipseType.HYBRID, "1948-05-09": EclipseType.HYBRID}
# Largest differences observed at the refined instants, rounded up (measured, not derived; see
# docs/working-notes, "Greatest eclipse: definitions"). NASA rounds its times to whole seconds.
OBSERVED_SOLAR_TIME_S = 0.55  # observed 0.548
OBSERVED_LUNAR_TIME_S = 2.75  # observed 2.750; beyond rounding, the cause is not investigated
OBSERVED_CENTRAL_SOLAR_MAGNITUDE = 0.002  # observed 0.0019; non-central ones differ up to 0.044
OBSERVED_UMBRAL_MAGNITUDE = 0.001  # observed 0.0008
OBSERVED_PENUMBRAL_MAGNITUDE = 0.0005  # observed 0.00044
# The defined quantities are not resolved below ~10 ms: a +-10 ms bracket around the minimum
# fails for a fifth of the canon's eclipses (measured), a +-0.1 s bracket for none.
BRACKET_S = 0.1
# Instants become UTC readings rounded to the microsecond.
MICROSECOND_ROUNDING_S = 0.5e-6


def jd_rounding_s(jd: float) -> float:
    """A few roundings of a Julian date near `jd`, in seconds."""
    return 4 * math.ulp(jd) * 86400


def same_instant(jd_tt: float) -> float:
    """Tolerance in days for one instant computed twice: two UTC and Julian-date roundings."""
    return 2 * (MICROSECOND_ROUNDING_S + jd_rounding_s(jd_tt)) * SECOND


def td_julian_day(row: dict[str, str]) -> float:
    year, month, day = (int(part) for part in row["date"].split("-"))
    hours, minutes, seconds = (int(part) for part in row["td_greatest"].split(":"))
    jd: float = swisseph.julday(year, month, day, hours + minutes / 60 + seconds / 3600)
    return jd


@pytest.fixture(scope="module")
def eph(fetched_ephe: Path) -> Ephemeris:
    return Ephemeris.open(fetched_ephe)


@pytest.fixture(scope="module")
def found(eph: Ephemeris) -> dict[str, list[tuple[dict[str, str], Eclipse]]]:
    """Every canon eclipse, as the engine finds it searching from two days before."""
    result = {}
    for kind in EclipseKind:
        types = SOLAR_TYPES if kind is EclipseKind.SOLAR else LUNAR_TYPES
        result[kind.value] = [
            (
                row,
                next_eclipse(
                    eph,
                    eph.time_from_tt(td_julian_day(row) - 2, TimeScale.CLOCK_AS_UT1),
                    kind,
                    types,
                ),
            )
            for row in CANON[kind.value]
        ]
    return result


def utc_time(
    eph: Ephemeris, utc: datetime, scale: TimeScale = TimeScale.CLOCK_AS_UT1
) -> AstronomicalTime:
    return eph.time(resolve(UtcTime(utc=utc), LONDON), scale)


# --- Against NASA's canon -------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["solar", "lunar"])
def test_every_canon_eclipse_is_found_with_its_type_and_saros(
    found: dict[str, list[tuple[dict[str, str], Eclipse]]], kind: str
) -> None:
    assert len(found[kind]) == {"solar": 452, "lunar": 457}[kind]
    wrong = []
    for row, eclipse in found[kind]:
        expected = LETTERS[kind][row["type"][0]]
        if kind == "solar":
            expected = TYPE_EXCEPTIONS.get(row["date"], expected)
        same = abs(eclipse.greatest.time.jd_tt - td_julian_day(row)) < 1 / 24
        if not same or eclipse.type is not expected or eclipse.saros_series != int(row["saros"]):
            wrong.append((row["date"], row["type"], eclipse.type, eclipse.saros_series))
    assert wrong == []


def test_solar_central_flags_match_the_canon(
    found: dict[str, list[tuple[dict[str, str], Eclipse]]],
) -> None:
    # NASA marks non-central total and annular eclipses with "+" or "-"; partials are never central.
    for row, eclipse in found["solar"]:
        central = row["type"][0] in "TAH" and not set(row["type"]) & {"+", "-"}
        assert eclipse.central is central, row["date"]


@pytest.mark.parametrize(
    ("kind", "bound_s"), [("solar", OBSERVED_SOLAR_TIME_S), ("lunar", OBSERVED_LUNAR_TIME_S)]
)
def test_greatest_eclipse_matches_the_canon(
    found: dict[str, list[tuple[dict[str, str], Eclipse]]], kind: str, bound_s: float
) -> None:
    worst = max(
        abs(eclipse.greatest.time.jd_tt - td_julian_day(row)) * 86400
        for row, eclipse in found[kind]
    )
    assert worst <= bound_s


def test_magnitudes_match_the_canon(found: dict[str, list[tuple[dict[str, str], Eclipse]]]) -> None:
    for row, eclipse in found["solar"]:
        if eclipse.central:
            assert eclipse.magnitude == pytest.approx(
                float(row["magnitude"]), abs=OBSERVED_CENTRAL_SOLAR_MAGNITUDE
            ), row["date"]
    for row, eclipse in found["lunar"]:
        assert eclipse.penumbral_magnitude == pytest.approx(
            float(row["penumbral_magnitude"]), abs=OBSERVED_PENUMBRAL_MAGNITUDE
        ), row["date"]
        if eclipse.type is EclipseType.PENUMBRAL:
            # Swiss Ephemeris reports no umbral magnitude for a penumbral eclipse, where NASA
            # prints a negative one.
            assert eclipse.magnitude == 0.0, row["date"]
        else:
            assert eclipse.magnitude == pytest.approx(
                float(row["umbral_magnitude"]), abs=OBSERVED_UMBRAL_MAGNITUDE
            ), row["date"]


# --- The instants against their definitions -------------------------------------------------

APPARENT_XYZ = swisseph.FLG_SWIEPH | swisseph.FLG_EQUATORIAL | swisseph.FLG_XYZ


def _vectors(jd_tt: float) -> tuple[list[float], list[float]]:
    sun = list(swisseph.calc(jd_tt, swisseph.SUN, APPARENT_XYZ)[0][:3])
    moon = list(swisseph.calc(jd_tt, swisseph.MOON, APPARENT_XYZ)[0][:3])
    return sun, moon


def _cross(a: list[float], b: list[float]) -> list[float]:
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _norm(a: list[float]) -> float:
    return math.sqrt(sum(x * x for x in a))


def solar_axis_distance(jd_tt: float) -> float:
    """Distance of Earth's centre from the shadow axis (through the Moon, away from the Sun)."""
    sun, moon = _vectors(jd_tt)
    axis = [m - s for m, s in zip(moon, sun, strict=True)]
    return _norm(_cross(moon, axis)) / _norm(axis)


def lunar_antisun_angle(jd_tt: float) -> float:
    """Geocentric angle between the Moon and the point opposite the Sun."""
    sun, moon = _vectors(jd_tt)
    antisun = [-s for s in sun]
    return math.atan2(
        _norm(_cross(moon, antisun)), sum(m * a for m, a in zip(moon, antisun, strict=True))
    )


@pytest.mark.parametrize(
    ("kind", "quantity"), [("solar", solar_axis_distance), ("lunar", lunar_antisun_angle)]
)
def test_greatest_eclipse_is_the_minimum_of_its_definition(
    found: dict[str, list[tuple[dict[str, str], Eclipse]]],
    kind: str,
    quantity: Callable[[float], float],
) -> None:
    misplaced = []
    for row, eclipse in found[kind]:
        t, h = eclipse.greatest.time.jd_tt, BRACKET_S * SECOND
        if not quantity(t - h) > quantity(t) < quantity(t + h):
            misplaced.append(row["date"])
    assert misplaced == []


@pytest.mark.parametrize(("kind", "target"), [("solar", 0.0), ("lunar", 180.0)])
def test_the_syzygy_is_the_exact_new_or_full_moon(
    found: dict[str, list[tuple[dict[str, str], Eclipse]]], kind: str, target: float
) -> None:
    for row, eclipse in found[kind]:
        sun, moon = eclipse.syzygy.sun, eclipse.syzygy.moon
        elongation = math.remainder(moon.longitude - sun.longitude - target, 360.0)
        rate_deg_per_s = (moon.longitude_speed - sun.longitude_speed) / 86400
        rounding_s = MICROSECOND_ROUNDING_S + jd_rounding_s(eclipse.syzygy.time.jd_tt)
        assert abs(elongation) <= rate_deg_per_s * rounding_s + 4 * math.ulp(360.0), row["date"]
        # The same eclipse: a syzygy is within hours of greatest eclipse, lunations are 29.5 days.
        assert abs(eclipse.syzygy.time.jd_tt - eclipse.greatest.time.jd_tt) < 0.5


def test_positions_at_each_instant_are_the_engines(
    eph: Ephemeris, found: dict[str, list[tuple[dict[str, str], Eclipse]]]
) -> None:
    for kind in ("solar", "lunar"):
        for _, eclipse in found[kind][::50]:
            for instant in (eclipse.greatest, eclipse.syzygy):
                positions = eph.positions(instant.time, (Body.SUN, Body.MOON))
                assert instant.sun == positions[Body.SUN]
                assert instant.moon == positions[Body.MOON]


# --- Nearest before and after ---------------------------------------------------------------


def test_the_nearest_eclipses_bracket_the_birth(eph: Ephemeris) -> None:
    birth = utc_time(eph, datetime(1987, 6, 15, 3, 25, tzinfo=UTC))
    nearest = nearest_eclipses(eph, birth, solar_types=SOLAR_TYPES, lunar_types=LUNAR_TYPES)
    for kind, before, after in (
        (EclipseKind.SOLAR, nearest.solar_before, nearest.solar_after),
        (EclipseKind.LUNAR, nearest.lunar_before, nearest.lunar_after),
    ):
        assert before.kind is after.kind is kind
        assert before.greatest.time.jd_tt < birth.jd_tt < after.greatest.time.jd_tt
        # Nothing of the kind in between: the next one after `before` is `after`.
        types = SOLAR_TYPES if kind is EclipseKind.SOLAR else LUNAR_TYPES
        following = next_eclipse(
            eph,
            eph.time_from_tt(before.greatest.time.jd_tt + SECOND, birth.time_scale),
            kind,
            types,
        )
        assert following.greatest.time.jd_tt == pytest.approx(
            after.greatest.time.jd_tt, abs=same_instant(after.greatest.time.jd_tt)
        )
        assert previous_eclipse(eph, birth, kind, types).greatest == before.greatest
        assert next_eclipse(eph, birth, kind, types).greatest == after.greatest


@pytest.mark.parametrize(
    ("kind", "near", "offset_s", "side"),
    [
        # Refined greatest eclipse 9.6 s after Swiss Ephemeris's own estimate (measured).
        (EclipseKind.SOLAR, datetime(2011, 6, 25, tzinfo=UTC), -5.0, "after"),
        (EclipseKind.SOLAR, datetime(2011, 6, 25, tzinfo=UTC), +1.0, "before"),
        # Refined greatest eclipse 7.3 s before Swiss Ephemeris's own estimate (measured).
        (EclipseKind.LUNAR, datetime(1998, 8, 1, tzinfo=UTC), +3.0, "before"),
        (EclipseKind.LUNAR, datetime(1998, 8, 1, tzinfo=UTC), -1.0, "after"),
    ],
)
def test_a_birth_seconds_from_greatest_eclipse_is_placed_by_the_refined_instant(
    eph: Ephemeris, kind: EclipseKind, near: datetime, offset_s: float, side: str
) -> None:
    types = SOLAR_TYPES if kind is EclipseKind.SOLAR else LUNAR_TYPES
    eclipse = next_eclipse(eph, utc_time(eph, near), kind, types)
    greatest = eclipse.greatest.time.jd_tt
    birth = eph.time_from_tt(greatest + offset_s * SECOND, TimeScale.CLOCK_AS_UT1)
    nearest = nearest_eclipses(eph, birth, solar_types=SOLAR_TYPES, lunar_types=LUNAR_TYPES)
    chosen = {
        (EclipseKind.SOLAR, "before"): nearest.solar_before,
        (EclipseKind.SOLAR, "after"): nearest.solar_after,
        (EclipseKind.LUNAR, "before"): nearest.lunar_before,
        (EclipseKind.LUNAR, "after"): nearest.lunar_after,
    }[(kind, side)]
    assert chosen.greatest.time.jd_tt == pytest.approx(greatest, abs=same_instant(greatest))


def test_the_chosen_types_decide_which_eclipses_count(eph: Ephemeris) -> None:
    # 2024-03-25 was a penumbral lunar eclipse and 2024-09-18 a partial one (NASA's canon);
    # 2024-04-08 a total solar eclipse and 2024-10-02 an annular one.
    birth = utc_time(eph, datetime(2024, 3, 20, tzinfo=UTC))
    every = nearest_eclipses(eph, birth, solar_types=SOLAR_TYPES, lunar_types=LUNAR_TYPES)
    umbral = nearest_eclipses(
        eph,
        birth,
        solar_types={EclipseType.ANNULAR},
        lunar_types={EclipseType.TOTAL, EclipseType.PARTIAL},
    )
    assert every.lunar_after.greatest.time.instant.utc.date().isoformat() == "2024-03-25"
    assert every.lunar_after.type is EclipseType.PENUMBRAL
    assert umbral.lunar_after.greatest.time.instant.utc.date().isoformat() == "2024-09-18"
    assert every.solar_after.greatest.time.instant.utc.date().isoformat() == "2024-04-08"
    assert umbral.solar_after.greatest.time.instant.utc.date().isoformat() == "2024-10-02"
    assert umbral.solar_types == frozenset({EclipseType.ANNULAR})
    assert umbral.lunar_types == frozenset({EclipseType.TOTAL, EclipseType.PARTIAL})


@pytest.mark.parametrize(
    ("solar_types", "lunar_types"),
    [
        (set(), LUNAR_TYPES),
        (SOLAR_TYPES, set()),
        ({EclipseType.PENUMBRAL}, LUNAR_TYPES),
        (SOLAR_TYPES, {EclipseType.ANNULAR}),
        (SOLAR_TYPES, {EclipseType.HYBRID}),
    ],
    ids=["no-solar", "no-lunar", "penumbral-solar", "annular-lunar", "hybrid-lunar"],
)
def test_eclipse_types_must_suit_their_kind(
    eph: Ephemeris, solar_types: set[EclipseType], lunar_types: set[EclipseType]
) -> None:
    birth = utc_time(eph, datetime(2024, 3, 20, tzinfo=UTC))
    with pytest.raises(InvalidEclipseTypes):
        nearest_eclipses(eph, birth, solar_types=solar_types, lunar_types=lunar_types)


def test_the_type_presets_are_every_type_of_each_kind() -> None:
    assert {
        EclipseType.TOTAL,
        EclipseType.ANNULAR,
        EclipseType.HYBRID,
        EclipseType.PARTIAL,
    } == SOLAR_TYPES
    assert {EclipseType.TOTAL, EclipseType.PARTIAL, EclipseType.PENUMBRAL} == LUNAR_TYPES


# --- Outside the ephemeris files ------------------------------------------------------------


def test_eclipses_outside_the_files_are_refused(eph: Ephemeris) -> None:
    # Swiss Ephemeris would compute them with its Moshier fallback and say nothing (observed).
    # The previous eclipses before 1800-01-20 are in late 1799; the next ones after 2399-09-01
    # fall on 2400-01-12 (lunar) and 2400-01-26 (solar), after the planet file ends (2400-01-10).
    early = utc_time(eph, datetime(1800, 1, 20, tzinfo=UTC))
    late = utc_time(eph, datetime(2399, 9, 1, tzinfo=UTC))
    for kind, types in ((EclipseKind.SOLAR, SOLAR_TYPES), (EclipseKind.LUNAR, LUNAR_TYPES)):
        with pytest.raises(DateOutsideEphemeris):
            previous_eclipse(eph, early, kind, types)
        assert next_eclipse(eph, early, kind, types).greatest.time.jd_tt > early.jd_tt
        with pytest.raises(DateOutsideEphemeris):
            next_eclipse(eph, late, kind, types)
        assert previous_eclipse(eph, late, kind, types).greatest.time.jd_tt < late.jd_tt
    with pytest.raises(DateOutsideEphemeris):
        nearest_eclipses(eph, early, solar_types=SOLAR_TYPES, lunar_types=LUNAR_TYPES)


# --- Time scales ----------------------------------------------------------------------------


def test_eclipse_instants_do_not_depend_on_the_time_scale(eph: Ephemeris) -> None:
    utc = datetime(1987, 6, 15, 3, 25, tzinfo=UTC)
    scales = (TimeScale.CLOCK_AS_UT1, TimeScale.UT1_FROM_DELTA_T, TimeScale.UT1_FROM_IERS)
    clock, modelled, iers = (
        next_eclipse(eph, utc_time(eph, utc, scale), EclipseKind.SOLAR, SOLAR_TYPES).greatest.time
        for scale in scales
    )
    rounding_s = same_instant(clock.jd_tt) / SECOND
    assert (clock.time_scale, modelled.time_scale, iers.time_scale) == scales
    for time in (modelled, iers):
        assert time.jd_tt == pytest.approx(clock.jd_tt, abs=same_instant(clock.jd_tt))
    # Against the clock convention, only the UTC reading differs: by UT1 - UTC, taken as zero.
    gap_s = (clock.instant.utc - modelled.instant.utc).total_seconds()
    assert gap_s == pytest.approx(modelled.ut1_minus_utc_s, abs=rounding_s)
    # The model and IERS both read TT's own UTC; only their UT1 differs.
    assert abs((iers.instant.utc - modelled.instant.utc).total_seconds()) <= rounding_s
    ut1_gap_s = (modelled.jd_ut1 - iers.jd_ut1) * 86400
    assert ut1_gap_s == pytest.approx(
        modelled.ut1_minus_utc_s - iers.ut1_minus_utc_s, abs=rounding_s
    )
    assert abs(ut1_gap_s) > 0.001  # on 1987-09-23 the model is 29 ms off IERS (measured)


def test_under_iers_an_eclipse_past_its_last_day_is_refused(eph: Ephemeris) -> None:
    # From 2026-09-01 the next solar eclipse, 2027-02-06, is past IERS's last value (2026-10-10):
    # it has no UT1 on this scale, so another must be named. The one before still has.
    birth = utc_time(eph, datetime(2026, 9, 1, tzinfo=UTC), TimeScale.UT1_FROM_IERS)
    with pytest.raises(DateOutsideUt1Data, match="2026-10-10"):
        nearest_eclipses(eph, birth, solar_types=SOLAR_TYPES, lunar_types=LUNAR_TYPES)
    before = previous_eclipse(eph, birth, EclipseKind.SOLAR, SOLAR_TYPES)
    assert before.greatest.time.time_scale is TimeScale.UT1_FROM_IERS


@pytest.mark.parametrize(
    ("scale", "jd_tt"),
    [
        (scale, jd_tt)
        for scale in TimeScale
        for jd_tt in (2396910.0, 2446961.6423, 2460409.2620, 2597450.5)
        # In 2400 only the clock convention has a reading: IERS's table ends in 2026 and the
        # model scale in 2033 (test_ut1_iers.py and test_astronomical_time.py cover those).
        if not (scale is not TimeScale.CLOCK_AS_UT1 and jd_tt == 2597450.5)
    ],
)
def test_a_tt_julian_date_becomes_a_utc_reading(
    eph: Ephemeris, scale: TimeScale, jd_tt: float
) -> None:
    time = eph.time_from_tt(jd_tt, scale)
    assert isinstance(time.instant.input, UtcTime)
    assert time.time_scale is scale
    assert time.jd_tt == pytest.approx(
        jd_tt, abs=(MICROSECOND_ROUNDING_S + jd_rounding_s(jd_tt)) * SECOND
    )


def test_an_instant_inside_a_leap_second_has_no_utc_reading(eph: Ephemeris) -> None:
    # 2016-12-31 23:59:60.5 UTC. The clock convention reads UT1, which has no leap seconds.
    jd_tt, _ = swisseph.utc_to_jd(2016, 12, 31, 23, 59, 60.5, swisseph.GREG_CAL)
    with pytest.raises(InstantInLeapSecond):
        eph.time_from_tt(jd_tt, TimeScale.UT1_FROM_DELTA_T)
    clock = eph.time_from_tt(jd_tt, TimeScale.CLOCK_AS_UT1)
    assert clock.jd_tt == pytest.approx(jd_tt, abs=same_instant(jd_tt))


# --- What a result holds --------------------------------------------------------------------


def test_solar_and_lunar_eclipses_carry_their_own_fields(eph: Ephemeris) -> None:
    birth = utc_time(eph, datetime(2024, 3, 20, tzinfo=UTC))
    nearest = nearest_eclipses(eph, birth, solar_types=SOLAR_TYPES, lunar_types=LUNAR_TYPES)
    solar, lunar = nearest.solar_after, nearest.lunar_after
    assert isinstance(solar.place_of_greatest, GeoPosition)
    assert isinstance(solar.central, bool)
    assert solar.penumbral_magnitude is None
    assert lunar.place_of_greatest is None
    assert lunar.central is None
    assert isinstance(lunar.penumbral_magnitude, float)
    # 2024-04-08: greatest eclipse at 25N 104W in NASA's canon, which prints whole degrees.
    assert solar.place_of_greatest.latitude == pytest.approx(25, abs=0.5)
    assert solar.place_of_greatest.longitude == pytest.approx(-104, abs=0.5)


def test_solar_attributes_are_swiss_ephemeris_at_greatest_eclipse(
    found: dict[str, list[tuple[dict[str, str], Eclipse]]],
) -> None:
    # Taken at the refined instant. Its rounding to the microsecond (under 0.2 ms with the Julian
    # date) moves the shadow by under a metre, 1e-5 degree (measured: no difference at all).
    for row, eclipse in found["solar"]:
        _, where, attr = swisseph.sol_eclipse_where(
            eclipse.greatest.time.jd_ut1, swisseph.FLG_SWIEPH
        )
        assert eclipse.place_of_greatest is not None
        assert eclipse.place_of_greatest.latitude == pytest.approx(where[1], abs=1e-5), row["date"]
        assert eclipse.place_of_greatest.longitude == pytest.approx(where[0], abs=1e-5), row["date"]
        assert eclipse.magnitude == pytest.approx(attr[8], abs=1e-9), row["date"]


def _sol_eclipse_where_at(time: AstronomicalTime) -> tuple[list[float], list[float]]:
    """Swiss Ephemeris's place and attributes at this instant's own UT1 and TT: its delta-T set
    to their difference for the one call."""
    swisseph.set_delta_t_userdef(time.jd_tt - time.jd_ut1)
    try:
        _, where, attr = swisseph.sol_eclipse_where(time.jd_ut1, swisseph.FLG_SWIEPH)
    finally:
        swisseph.set_delta_t_userdef(swisseph.DELTAT_AUTOMATIC)
    return where, attr


@pytest.mark.parametrize(
    "birth",
    [
        datetime(1987, 6, 15, 3, 25, tzinfo=UTC),
        datetime(2024, 3, 20, tzinfo=UTC),
        datetime(2026, 6, 1, tzinfo=UTC),
    ],
    ids=["1987", "2024", "2026"],
)
def test_under_iers_solar_attributes_use_the_instant_s_own_ut1_and_tt(
    eph: Ephemeris, birth: datetime
) -> None:
    # Swiss Ephemeris takes UT and applies its own delta-T model, 0.34 s off IERS by 2026-08-12.
    # Left in place, that moves the place of greatest eclipse by up to 0.0011 degrees (latitude
    # or longitude) on 2024-04-08 and 0.0029 on 2026-08-12, and the magnitude by 3.6e-8
    # (measured).
    nearest = nearest_eclipses(
        eph,
        utc_time(eph, birth, TimeScale.UT1_FROM_IERS),
        solar_types=SOLAR_TYPES,
        lunar_types=LUNAR_TYPES,
    )
    for eclipse in (nearest.solar_before, nearest.solar_after):
        time = eclipse.greatest.time
        assert time.time_scale is TimeScale.UT1_FROM_IERS
        where, attr = _sol_eclipse_where_at(time)
        assert eclipse.place_of_greatest is not None
        assert eclipse.place_of_greatest.latitude == pytest.approx(where[1], abs=1e-5)
        assert eclipse.place_of_greatest.longitude == pytest.approx(where[0], abs=1e-5)
        assert eclipse.magnitude == pytest.approx(attr[8], abs=1e-9)


def test_the_result_records_its_inputs_and_conventions(eph: Ephemeris) -> None:
    birth = utc_time(eph, datetime(2024, 3, 20, tzinfo=UTC))
    nearest = nearest_eclipses(eph, birth, solar_types=SOLAR_TYPES, lunar_types=LUNAR_TYPES)
    assert nearest.birth is birth
    assert nearest.ephemeris == eph.provenance
    assert nearest.conventions == ECLIPSE_CONVENTIONS
    assert ECLIPSE_CONVENTIONS.startswith("eclipses-v1:")
    for eclipse in (
        nearest.solar_before,
        nearest.solar_after,
        nearest.lunar_before,
        nearest.lunar_after,
    ):
        assert eclipse.greatest.time.time_scale is birth.time_scale


def test_eclipses_refuse_after_the_data_path_moves(
    eph: Ephemeris, fetched_ephe: Path, tmp_path: Path
) -> None:
    birth = utc_time(eph, datetime(2024, 3, 20, tzinfo=UTC))
    other = tmp_path / "ephe"
    other.mkdir()
    for entry in load_manifest().files:
        shutil.copy2(fetched_ephe / entry.name, other / entry.name)
    verify_ephemeris(other)
    with pytest.raises(StaleEphemeris):
        next_eclipse(eph, birth, EclipseKind.SOLAR, SOLAR_TYPES)
    with pytest.raises(StaleEphemeris):
        eph.time_from_tt(birth.jd_tt, TimeScale.CLOCK_AS_UT1)
    Ephemeris.open(fetched_ephe)  # restore the process-wide path for later tests
