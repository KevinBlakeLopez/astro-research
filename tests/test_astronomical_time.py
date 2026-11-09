"""Time stage 2: resolved UTC instant -> Julian dates for Swiss Ephemeris, and the handle.

These tests name a time scale in every call. The IERS scale, the default, has its own module,
test_ut1_iers.py.
"""

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import swisseph

from astro_research.astronomical_time import AstronomicalTime, TimeScale, Ut1Basis
from astro_research.civil_time import ResolvedInstant, UtcTime, resolve
from astro_research.ephemeris import (
    DateOutsideEphemeris,
    DateOutsideLeapSeconds,
    Ephemeris,
    EphemerisNotLoaded,
    StaleEphemeris,
    load_manifest,
    verify_ephemeris,
)
from astro_research.geo import GeoPosition

GREENWICH = GeoPosition(latitude=51.4769, longitude=0.0)
# A float Julian date near the present resolves about 40 microseconds (2**-31 days).
JD_RESOLUTION_S = 5e-5


def instant(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> ResolvedInstant:
    return resolve(UtcTime(utc=datetime(year, month, day, hour, minute, tzinfo=UTC)), GREENWICH)


@pytest.fixture
def eph(fetched_ephe: Path) -> Ephemeris:
    return Ephemeris.open(fetched_ephe)


# --- The two conventions ------------------------------------------------------------------


def test_clock_as_ut1_uses_the_clock_reading_unchanged(eph: Ephemeris) -> None:
    time = eph.time(instant(2000, 1, 1), TimeScale.CLOCK_AS_UT1)
    assert time.jd_ut1 == 2451545.0
    assert time.ut1_minus_utc_s == 0.0
    assert time.time_scale is TimeScale.CLOCK_AS_UT1


@pytest.mark.parametrize(
    ("date", "tai_minus_utc_s"),
    [((1980, 1, 1), 19), ((2000, 1, 1), 32), ((2024, 7, 1), 37)],
)
def test_ut1_from_delta_t_puts_tt_exactly_on_the_leap_second_scale(
    eph: Ephemeris, date: tuple[int, int, int], tai_minus_utc_s: int
) -> None:
    # TT = UTC + (TAI - UTC) + 32.184 s, with TAI - UTC the published leap-second count. This
    # holds whatever the delta-T model says, so it is an independent check of the convention.
    time = eph.time(instant(*date), TimeScale.UT1_FROM_DELTA_T)
    clock = eph.time(instant(*date), TimeScale.CLOCK_AS_UT1)
    tt_minus_utc_s = (time.jd_tt - clock.jd_ut1) * 86400
    assert tt_minus_utc_s == pytest.approx(tai_minus_utc_s + 32.184, abs=JD_RESOLUTION_S)


def test_ut1_offset_is_recorded_after_1972(eph: Ephemeris) -> None:
    time = eph.time(instant(2000, 1, 1), TimeScale.UT1_FROM_DELTA_T)
    clock = eph.time(instant(2000, 1, 1), TimeScale.CLOCK_AS_UT1)
    assert time.ut1_minus_utc_s == pytest.approx(
        (time.jd_ut1 - clock.jd_ut1) * 86400, abs=JD_RESOLUTION_S
    )
    assert abs(time.ut1_minus_utc_s) < 0.9  # leap seconds keep |UT1 - UTC| below 0.9 s


@pytest.mark.parametrize("year", [1800, 1900, 1950, 1971])
def test_conventions_agree_before_1972(eph: Ephemeris, year: int) -> None:
    # Before leap-second UTC (1972), civil time was based on UT itself.
    a = eph.time(instant(year, 6, 1), TimeScale.CLOCK_AS_UT1)
    b = eph.time(instant(year, 6, 1), TimeScale.UT1_FROM_DELTA_T)
    assert (a.jd_ut1, a.jd_tt, a.ut1_minus_utc_s) == (b.jd_ut1, b.jd_tt, b.ut1_minus_utc_s)


# --- Where the model scale ends -----------------------------------------------------------

MODEL = TimeScale.UT1_FROM_DELTA_T
SWITCH_DAY = datetime(2033, 9, 17, tzinfo=UTC)


def at_utc(eph: Ephemeris, utc: datetime, scale: TimeScale = MODEL) -> AstronomicalTime:
    return eph.time(resolve(UtcTime(utc=utc), GREENWICH), scale)


def test_the_model_scale_ends_where_swiss_ephemeris_stops_applying_leap_seconds(
    eph: Ephemeris,
) -> None:
    # From 2033-09-17 Swiss Ephemeris's delta-T model puts UT1 - UTC past -1 s, so utc_to_jd
    # assumes leap seconds are missing and takes the reading as UT1 (observed). Across that day
    # one second of readings would be two of TT. The model scale refuses instead.
    last = at_utc(eph, SWITCH_DAY - timedelta(seconds=1))
    assert last.ut1_basis is Ut1Basis.DELTA_T_MODEL
    assert last.ut1_minus_utc_s == pytest.approx(-1.0002, abs=0.0001)  # measured -1.000196
    for utc in (SWITCH_DAY, datetime(2050, 1, 1, tzinfo=UTC)):
        with pytest.raises(DateOutsideLeapSeconds, match="2033-09-17") as refused:
            at_utc(eph, utc)
        assert "clock_as_ut1" in str(refused.value)  # the scale that does what it would have
    assert issubclass(DateOutsideLeapSeconds, DateOutsideEphemeris)


def test_from_the_switch_the_clock_convention_is_what_swiss_ephemeris_did(eph: Ephemeris) -> None:
    tt, ut1 = swisseph.utc_to_jd(2033, 9, 17, 0, 0, 0.0, swisseph.GREG_CAL)
    clock = at_utc(eph, SWITCH_DAY, TimeScale.CLOCK_AS_UT1)
    assert (clock.jd_ut1, clock.jd_tt) == (ut1, tt)  # observed: identical


def test_near_the_switch_a_julian_date_converts_back_or_is_refused(eph: Ephemeris) -> None:
    # jdet_to_utc tests for the switch at the instant, utc_to_jd at the date's 0h. So from
    # 2033-09-16 15:59:27 UTC to the end of that day, the reading Swiss Ephemeris gives for a TT
    # is a second early (measured). A reading that doesn't convert back to its TT is refused.
    before = at_utc(eph, datetime(2033, 9, 16, 15, tzinfo=UTC))
    back = eph.time_from_tt(before.jd_tt, MODEL)
    assert abs((back.instant.utc - before.instant.utc).total_seconds()) < 1e-4
    for utc in (datetime(2033, 9, 16, 16, tzinfo=UTC), SWITCH_DAY - timedelta(seconds=1)):
        late = at_utc(eph, utc)  # the reading is accepted, but its TT has no consistent reading
        with pytest.raises(DateOutsideLeapSeconds):
            eph.time_from_tt(late.jd_tt, MODEL)
    beyond = at_utc(eph, datetime(2034, 1, 1, tzinfo=UTC), TimeScale.CLOCK_AS_UT1)
    with pytest.raises(DateOutsideLeapSeconds):
        eph.time_from_tt(beyond.jd_tt, MODEL)


# --- What gets recorded -------------------------------------------------------------------


@pytest.mark.parametrize("time_scale", list(TimeScale))
def test_recorded_delta_t_links_the_two_julian_dates(eph: Ephemeris, time_scale: TimeScale) -> None:
    time = eph.time(instant(1900, 1, 1, hour=0), time_scale)
    linked_s = (time.jd_tt - time.jd_ut1) * 86400
    assert linked_s == pytest.approx(time.delta_t_s, abs=JD_RESOLUTION_S)


@pytest.mark.parametrize("year", [1800, 1900, 2000])
def test_recorded_tt_is_the_one_positions_will_use(eph: Ephemeris, year: int) -> None:
    # Before 1955 delta-T depends on the loaded DE (DE441 vs DE431: 0.30 s at 1800), and
    # utc_to_jd's own TT uses the DE431 value. The recorded TT must match what Swiss Ephemeris
    # applies when it computes from UT. The Moon moves ~0.5 arcsec/s, so 1 ms shows.
    time = eph.time(instant(year, 6, 1), TimeScale.UT1_FROM_DELTA_T)
    from_ut, _, _ = swisseph.calc_ut(time.jd_ut1, swisseph.MOON, swisseph.FLG_SWIEPH)
    from_tt, _, _ = swisseph.calc(time.jd_tt, swisseph.MOON, swisseph.FLG_SWIEPH)
    assert from_ut[0] == pytest.approx(from_tt[0], abs=1e-7)  # 0.36 mas


def test_original_instant_travels_with_the_time(eph: Ephemeris) -> None:
    given = instant(2000, 1, 1)
    assert eph.time(given, TimeScale.CLOCK_AS_UT1).instant is given


def test_sub_second_differences_survive(eph: Ephemeris) -> None:
    a = resolve(UtcTime(utc=datetime(2000, 1, 1, 12, 0, 0, 0, tzinfo=UTC)), GREENWICH)
    b = resolve(UtcTime(utc=datetime(2000, 1, 1, 12, 0, 0, 500_000, tzinfo=UTC)), GREENWICH)
    for scale in TimeScale:
        gap_s = (eph.time(b, scale).jd_ut1 - eph.time(a, scale).jd_ut1) * 86400
        assert gap_s == pytest.approx(0.5, abs=JD_RESOLUTION_S)


# --- The handle ---------------------------------------------------------------------------


def test_handle_cannot_be_built_without_verification(fetched_ephe: Path) -> None:
    # Real arguments, wrong token: only Ephemeris.open() can build a handle.
    with pytest.raises(TypeError):
        Ephemeris(fetched_ephe.resolve(), verify_ephemeris(fetched_ephe), object())


def test_handle_carries_the_verified_provenance(fetched_ephe: Path) -> None:
    assert Ephemeris.open(fetched_ephe).provenance == verify_ephemeris(fetched_ephe)


def test_handle_refuses_after_the_data_path_moves(
    eph: Ephemeris, fetched_ephe: Path, tmp_path: Path
) -> None:
    # Swiss Ephemeris has one process-wide data path. Verifying another folder moves it.
    other = tmp_path / "ephe"
    other.mkdir()
    for entry in load_manifest().files:
        shutil.copy2(fetched_ephe / entry.name, other / entry.name)
    verify_ephemeris(other)
    with pytest.raises(StaleEphemeris):
        eph.time(instant(2000, 1, 1), TimeScale.CLOCK_AS_UT1)
    fresh = Ephemeris.open(fetched_ephe)  # reopening restores a working handle
    assert fresh.time(instant(2000, 1, 1), TimeScale.CLOCK_AS_UT1).jd_ut1 == 2451545.0


def test_handle_goes_stale_even_when_another_verification_fails(
    eph: Ephemeris, fetched_ephe: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failed verification has already moved the process-wide path before it fails.
    other = tmp_path / "ephe"
    other.mkdir()
    for entry in load_manifest().files:
        shutil.copy2(fetched_ephe / entry.name, other / entry.name)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("SE_EPHE_PATH", str(empty))
    with pytest.raises(EphemerisNotLoaded):
        verify_ephemeris(other)
    monkeypatch.delenv("SE_EPHE_PATH")
    with pytest.raises(StaleEphemeris):
        eph.time(instant(2000, 1, 1), TimeScale.CLOCK_AS_UT1)


def test_handle_refuses_while_se_ephe_path_is_set(
    eph: Ephemeris, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # SE_EPHE_PATH overrides the verified folder for any file Swiss Ephemeris opens later.
    monkeypatch.setenv("SE_EPHE_PATH", str(tmp_path))
    with pytest.raises(EphemerisNotLoaded):
        eph.time(instant(2000, 1, 1), TimeScale.CLOCK_AS_UT1)
