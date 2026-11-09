"""UT1 from IERS: the default time scale.

`TimeScale.UT1_FROM_IERS` takes UT1 - UTC from IERS's measured EOP 20 C04 series, pinned by hash
in the package. Between IERS's daily 0h values, delta-T (TT - UT1, which has no leap-second
steps) is interpolated linearly in TT; TT itself comes exactly from UTC through leap seconds.
Before 1972 civil time is taken as UT, as both other scales do; after IERS's last day the scale
refuses, and another must be named.

Swiss Ephemeris's own model (`UT1_FROM_DELTA_T`) agrees with IERS within 0.066 s for 1972-2022
but drifts to 0.40 s by 2026, because its delta-T table is extrapolated after 2023 (measured).
The reference values below are IERS's, copied from eopc04.1962-now (Last-Modified 2026-11-09).
"""

import dataclasses
import hashlib
import importlib.resources
import math
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import swisseph

from astro_research import ephemeris
from astro_research.astronomical_time import AstronomicalTime, TimeScale, Ut1Basis
from astro_research.chart import BirthRecord, ChartSettings, calculation_inputs
from astro_research.civil_time import UtcTime, resolve
from astro_research.eclipses import SOLAR_TYPES, EclipseKind, next_eclipse
from astro_research.ephemeris import (
    DateOutsideEphemeris,
    DateOutsideUt1Data,
    Ephemeris,
    EphemerisFileError,
    load_manifest,
    verify_ut1_data,
)
from astro_research.geo import GeoPosition
from astro_research.measurements import MAJOR
from astro_research.parans import Disc, HorizonConvention, angular_events
from astro_research.positions import Body

LONDON = GeoPosition(latitude=51.5074, longitude=-0.1278)
IERS = TimeScale.UT1_FROM_IERS
# UT1 - UTC at 0h UTC, as IERS publishes it.
IERS_VALUES = {
    datetime(1972, 1, 1, tzinfo=UTC): -0.0454859,
    datetime(1987, 6, 15, tzinfo=UTC): -0.3838893,
    datetime(1987, 6, 16, tzinfo=UTC): -0.3850248,
    datetime(2000, 1, 1, tzinfo=UTC): 0.3554724,
    datetime(2016, 12, 31, tzinfo=UTC): -0.4077697,
    datetime(2017, 1, 1, tzinfo=UTC): 0.5912870,
    datetime(2024, 3, 20, tzinfo=UTC): -0.0091683,
    datetime(2024, 3, 21, tzinfo=UTC): -0.0094079,
    datetime(2026, 10, 10, tzinfo=UTC): -0.0315785,
}
TAI_MINUS_UTC = {1972: 10, 1987: 23, 2000: 32, 2016: 36, 2017: 37, 2024: 37, 2026: 37}
LAST_DAY = datetime(2026, 10, 10, tzinfo=UTC)


@pytest.fixture
def eph(fetched_ephe: Path) -> Ephemeris:
    return Ephemeris.open(fetched_ephe)


def at(eph: Ephemeris, utc: datetime, scale: TimeScale = IERS) -> AstronomicalTime:
    return eph.time(resolve(UtcTime(utc=utc), LONDON), scale)


def rounding_s(time: AstronomicalTime) -> float:
    """A few roundings of the Julian dates involved, in seconds (about 40 us each)."""
    return 4 * math.ulp(time.jd_tt) * 86400


def packaged_ut1_file() -> Path:
    return Path(str(importlib.resources.files("astro_research") / load_manifest().ut1_data.name))


# --- The pinned data ------------------------------------------------------------------------


def test_the_ut1_data_is_pinned_by_hash() -> None:
    entry = load_manifest().ut1_data
    data = packaged_ut1_file().read_bytes()
    assert hashlib.sha256(data).hexdigest() == entry.sha256
    assert entry.source_url == "https://hpiers.obspm.fr/iers/eop/eopc04/eopc04.1962-now"
    assert entry.source_sha256 == "5e98a6a8a9da876d94f735b17f557947048c8dded116e05c8433a1167860fb90"
    assert (entry.first_day, entry.last_day) == ("1972-01-01", "2026-10-10")
    header = data.decode().splitlines()[:12]
    assert any(entry.source_url in line for line in header)
    assert any(entry.source_sha256 in line for line in header)
    assert any("IERS" in line for line in header)


def test_an_altered_ut1_file_is_refused(tmp_path: Path) -> None:
    altered = tmp_path / "ut1.txt"
    shutil.copy2(packaged_ut1_file(), altered)
    text = altered.read_text()
    altered.write_text(text.replace("-0.0454859", "-0.0454860", 1))
    with pytest.raises(EphemerisFileError, match="ut1"):
        verify_ut1_data(altered)
    verify_ut1_data(packaged_ut1_file())  # the packaged copy passes


def test_opening_the_ephemeris_checks_the_ut1_data(
    fetched_ephe: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Checked when the environment is, not only when the scale is first used.
    manifest = load_manifest()
    wrong = dataclasses.replace(
        manifest, ut1_data=dataclasses.replace(manifest.ut1_data, sha256="0" * 64)
    )
    monkeypatch.setattr(ephemeris, "load_manifest", lambda: wrong)
    ephemeris._packaged_ut1.cache_clear()  # the table is read once per process
    try:
        with pytest.raises(EphemerisFileError, match="ut1"):
            Ephemeris.open(fetched_ephe)
    finally:
        ephemeris._packaged_ut1.cache_clear()


def test_the_ut1_data_is_part_of_the_provenance(eph: Ephemeris) -> None:
    entry = load_manifest().ut1_data
    assert eph.provenance.ut1_data_sha256 == entry.sha256
    settings = ChartSettings(aspect_angles=MAJOR)
    record_time = UtcTime(utc=datetime(2000, 1, 1, 12, tzinfo=UTC))
    inputs = calculation_inputs(
        BirthRecord(time=record_time, place=LONDON), settings, eph.provenance
    )
    assert entry.sha256 in inputs


# --- Values ---------------------------------------------------------------------------------


@pytest.mark.parametrize("utc", list(IERS_VALUES), ids=lambda d: f"{d:%Y-%m-%d}")
def test_at_0h_ut1_minus_utc_is_iers_own_value(eph: Ephemeris, utc: datetime) -> None:
    time = at(eph, utc)
    assert time.ut1_minus_utc_s == pytest.approx(IERS_VALUES[utc], abs=rounding_s(time))
    assert time.ut1_basis is Ut1Basis.IERS_C04


@pytest.mark.parametrize(
    ("utc", "day", "fraction"),
    [
        (datetime(1987, 6, 15, 12, tzinfo=UTC), datetime(1987, 6, 15, tzinfo=UTC), 0.5),
        (datetime(2024, 3, 20, 6, tzinfo=UTC), datetime(2024, 3, 20, tzinfo=UTC), 0.25),
    ],
)
def test_between_days_the_values_are_interpolated_linearly(
    eph: Ephemeris, utc: datetime, day: datetime, fraction: float
) -> None:
    # No leap second on these days, so UT1 - UTC itself is linear across them.
    following = day.replace(day=day.day + 1)
    expected = (1 - fraction) * IERS_VALUES[day] + fraction * IERS_VALUES[following]
    time = at(eph, utc)
    assert time.ut1_minus_utc_s == pytest.approx(expected, abs=rounding_s(time))


def test_across_a_leap_second_ut1_stays_continuous(eph: Ephemeris) -> None:
    # 2016-12-31 ended with 23:59:60. UT1 - UTC jumps by a whole second there, but UT1 - TAI
    # (and so delta-T) is smooth: the interpolation runs across it without a step.
    day, next_day = datetime(2016, 12, 31, tzinfo=UTC), datetime(2017, 1, 1, tzinfo=UTC)
    ut1_minus_tai = (IERS_VALUES[day] - 36, IERS_VALUES[next_day] - 37)
    noon = at(eph, datetime(2016, 12, 31, 12, tzinfo=UTC))
    # Noon UTC is half a day plus nothing from the leap second, which comes at the end.
    expected = (ut1_minus_tai[0] + ut1_minus_tai[1]) / 2 + 36
    # The day has 86 401 s, so noon is 43 200/86 401 of it, not 1/2: under 10 ns here.
    assert noon.ut1_minus_utc_s == pytest.approx(expected, abs=rounding_s(noon))
    before = at(eph, datetime(2016, 12, 31, 23, 59, 59, tzinfo=UTC))
    after = at(eph, datetime(2017, 1, 1, 0, 0, 0, tzinfo=UTC))
    # Two seconds of elapsed time (23:59:59, then 23:59:60) between the two readings.
    assert (after.jd_ut1 - before.jd_ut1) * 86400 == pytest.approx(2.0, abs=rounding_s(after))


@pytest.mark.parametrize(
    "utc", [datetime(1987, 6, 15, 3, 25, tzinfo=UTC), datetime(2024, 3, 20, 18, tzinfo=UTC)]
)
def test_tt_is_exact_and_delta_t_is_tt_minus_ut1(eph: Ephemeris, utc: datetime) -> None:
    time = at(eph, utc)
    clock = at(eph, utc, TimeScale.CLOCK_AS_UT1).jd_ut1  # the bare UTC reading as a Julian date
    tt_minus_utc_s = (time.jd_tt - clock) * 86400
    assert tt_minus_utc_s == pytest.approx(TAI_MINUS_UTC[utc.year] + 32.184, abs=rounding_s(time))
    assert time.delta_t_s == pytest.approx((time.jd_tt - time.jd_ut1) * 86400, abs=rounding_s(time))
    assert time.delta_t_s == pytest.approx(
        TAI_MINUS_UTC[utc.year] + 32.184 - time.ut1_minus_utc_s, abs=rounding_s(time)
    )


def test_tt_agrees_with_the_delta_t_scale_and_ut1_differs_by_the_model_error(
    eph: Ephemeris,
) -> None:
    # Both scales take TT exactly from UTC; only UT1 differs, by the model's error (2026-10-10:
    # IERS -0.0316 s, Swiss Ephemeris's model +0.3673 s, measured).
    iers, model = at(eph, LAST_DAY), at(eph, LAST_DAY, TimeScale.UT1_FROM_DELTA_T)
    assert iers.jd_tt == pytest.approx(model.jd_tt, abs=rounding_s(iers) / 86400)
    assert model.ut1_minus_utc_s - iers.ut1_minus_utc_s == pytest.approx(0.3989, abs=0.001)


# --- Coverage -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utc",
    [datetime(1950, 6, 1, 12, tzinfo=UTC), datetime(1971, 12, 31, 12, tzinfo=UTC)],
    ids=["1950", "1971-12-31"],
)
def test_before_1972_civil_time_is_taken_as_ut(eph: Ephemeris, utc: datetime) -> None:
    # As both other scales do: IERS's 1962-1971 values are against the old, rubber-second UTC.
    iers, clock = at(eph, utc), at(eph, utc, TimeScale.CLOCK_AS_UT1)
    assert (iers.jd_ut1, iers.jd_tt, iers.delta_t_s) == (clock.jd_ut1, clock.jd_tt, clock.delta_t_s)
    assert iers.ut1_minus_utc_s == 0.0
    assert iers.ut1_basis is Ut1Basis.CIVIL_TIME_AS_UT


def test_after_iers_last_day_the_scale_refuses(eph: Ephemeris) -> None:
    assert at(eph, LAST_DAY).ut1_basis is Ut1Basis.IERS_C04  # its last value is usable
    for utc in (LAST_DAY.replace(second=1), datetime(2027, 1, 1, tzinfo=UTC)):
        with pytest.raises(DateOutsideUt1Data, match="2026-10-10"):
            at(eph, utc)
        assert at(eph, utc, TimeScale.UT1_FROM_DELTA_T).jd_ut1 > 0  # another scale still works
    assert issubclass(DateOutsideUt1Data, DateOutsideEphemeris)


@pytest.mark.parametrize(
    ("scale", "year", "basis"),
    [
        (TimeScale.CLOCK_AS_UT1, 1987, Ut1Basis.CLOCK_READING),
        (TimeScale.CLOCK_AS_UT1, 1950, Ut1Basis.CLOCK_READING),
        (TimeScale.UT1_FROM_DELTA_T, 1987, Ut1Basis.DELTA_T_MODEL),
        (TimeScale.UT1_FROM_DELTA_T, 1950, Ut1Basis.CIVIL_TIME_AS_UT),
        (TimeScale.UT1_FROM_IERS, 1987, Ut1Basis.IERS_C04),
        (TimeScale.UT1_FROM_IERS, 1950, Ut1Basis.CIVIL_TIME_AS_UT),
    ],
)
def test_each_time_records_where_its_ut1_came_from(
    eph: Ephemeris, scale: TimeScale, year: int, basis: Ut1Basis
) -> None:
    assert at(eph, datetime(year, 6, 15, 3, 25, tzinfo=UTC), scale).ut1_basis is basis


# --- Conversions back from Julian dates -----------------------------------------------------


@pytest.mark.parametrize(
    "utc", [datetime(1987, 6, 15, 3, 25, tzinfo=UTC), datetime(2024, 3, 20, 18, tzinfo=UTC)]
)
def test_julian_dates_convert_back_consistently(eph: Ephemeris, utc: datetime) -> None:
    time = at(eph, utc)
    tolerance = (0.5e-6 + rounding_s(time)) / 86400  # the UTC reading is kept to the microsecond
    from_tt = eph.time_from_tt(time.jd_tt, IERS)
    from_ut1 = eph.time_from_ut1(time.jd_ut1, IERS)
    for back in (from_tt, from_ut1):
        assert back.time_scale is IERS
        assert back.jd_tt == pytest.approx(time.jd_tt, abs=tolerance)
        assert back.jd_ut1 == pytest.approx(time.jd_ut1, abs=tolerance)
        assert abs((back.instant.utc - utc).total_seconds()) <= tolerance * 86400


def test_a_julian_date_past_the_last_day_is_refused(eph: Ephemeris) -> None:
    beyond = at(eph, datetime(2027, 1, 1, tzinfo=UTC), TimeScale.UT1_FROM_DELTA_T)
    with pytest.raises(DateOutsideUt1Data):
        eph.time_from_tt(beyond.jd_tt, IERS)
    with pytest.raises(DateOutsideUt1Data):
        eph.time_from_ut1(beyond.jd_ut1, IERS)


# --- Swiss Ephemeris's own delta-T ----------------------------------------------------------


def _delta_t_is_the_model() -> bool:
    return bool(swisseph.deltat(2415020.5) != swisseph.deltat(2451545.0))  # 1900 and 2000


def test_a_failure_inside_swiss_ephemeris_leaves_its_delta_t_as_the_model(
    eph: Ephemeris, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Swiss Ephemeris applies its own delta-T model to UT, so IERS calculations set their own
    # delta-T for each call. A constant left behind would silently become every later
    # calculation's delta-T (conftest.py checks after every test); here, after a failure.
    def failing(*_: object) -> None:
        raise RuntimeError("inside Swiss Ephemeris")

    start, end = (
        at(eph, datetime(2024, 3, 20, tzinfo=UTC)),
        at(eph, datetime(2024, 3, 21, tzinfo=UTC)),
    )
    horizon = HorizonConvention(disc=Disc.CENTRE, refraction=None)
    monkeypatch.setattr(swisseph, "rise_trans", failing)
    monkeypatch.setattr(swisseph, "sol_eclipse_where", failing)
    with pytest.raises(RuntimeError, match="inside Swiss Ephemeris"):
        angular_events(
            eph, LONDON, start=start, end=end, bodies=(Body.MOON,), stars=(), horizon=horizon
        )
    assert _delta_t_is_the_model()
    with pytest.raises(RuntimeError, match="inside Swiss Ephemeris"):
        next_eclipse(eph, start, EclipseKind.SOLAR, SOLAR_TYPES)
    assert _delta_t_is_the_model()


# --- The default ----------------------------------------------------------------------------


def test_iers_is_the_default_time_scale() -> None:
    assert ChartSettings(aspect_angles=MAJOR).time_scale is TimeScale.UT1_FROM_IERS
