"""Civil time -> UTC instant resolution.

Fixtures are synthetic: public city coordinates and constructed times, no real subjects.
Zone behaviour asserted here was first observed with zoneinfo against tzdata 2026.6
(see the Phase 1 report for the exploratory runs).
"""

import importlib.metadata
import importlib.resources
import pickle
import zoneinfo
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from astro_research.civil_time import (
    AmbiguousLocalTime,
    CivilTimeError,
    LocalMeanTime,
    NonexistentLocalTime,
    OffsetLocalTime,
    OffsetSource,
    Pre1970TzdbNotAccepted,
    StatedOffsetMismatch,
    TzdbLocalMeanTime,
    UnknownTimeZone,
    UnsupportedInput,
    UtcTime,
    ZonedLocalTime,
    resolve,
)
from astro_research.geo import GeoPosition

NEW_YORK = GeoPosition(latitude=40.7128, longitude=-74.0060)
CHICAGO = GeoPosition(latitude=41.8781, longitude=-87.6298)
BUFFALO = GeoPosition(latitude=42.8864, longitude=-78.8784)
SYDNEY = GeoPosition(latitude=-33.8688, longitude=151.2093)
APIA = GeoPosition(latitude=-13.8333, longitude=-171.7500)
AMSTERDAM = GeoPosition(latitude=52.3676, longitude=4.9041)
KATHMANDU = GeoPosition(latitude=27.7172, longitude=85.3240)


def hours(h: float) -> timedelta:
    return timedelta(hours=h)


# --- Explicit UTC -------------------------------------------------------------------------


def test_utc_input_passes_through_with_microseconds() -> None:
    given = datetime(2000, 1, 1, 12, 0, 0, 123456, tzinfo=UTC)
    resolved = resolve(UtcTime(utc=given), NEW_YORK)
    assert resolved.utc == given
    assert resolved.utc_offset == timedelta(0)
    assert resolved.offset_source is OffsetSource.UTC_GIVEN
    assert resolved.zone_abbreviation is None
    assert resolved.tzdata_version is None


def test_utc_input_must_be_aware() -> None:
    with pytest.raises(UnsupportedInput):
        UtcTime(utc=datetime(2000, 1, 1, 12))


def test_utc_input_must_have_zero_offset() -> None:
    # A non-UTC aware datetime is a stated offset, which has its own input type.
    with pytest.raises(UnsupportedInput):
        UtcTime(utc=datetime(2000, 1, 1, 12, tzinfo=timezone(hours(1))))


# --- Zone rules: ordinary times -----------------------------------------------------------


def test_zoned_summer_time() -> None:
    time = ZonedLocalTime(local=datetime(2023, 7, 1, 12, 0), zone="America/New_York")
    resolved = resolve(time, NEW_YORK)
    assert resolved.utc == datetime(2023, 7, 1, 16, 0, tzinfo=UTC)
    assert resolved.utc_offset == hours(-4)
    assert resolved.offset_source is OffsetSource.TZDB
    assert resolved.zone_abbreviation == "EDT"


def test_original_input_is_preserved() -> None:
    time = ZonedLocalTime(local=datetime(2023, 7, 1, 12, 0), zone="America/New_York")
    assert resolve(time, NEW_YORK).input is time


def test_zoned_time_keeps_microseconds() -> None:
    time = ZonedLocalTime(local=datetime(2023, 7, 1, 12, 0, 0, 123456), zone="America/New_York")
    assert resolve(time, NEW_YORK).utc == datetime(2023, 7, 1, 16, 0, 0, 123456, tzinfo=UTC)


def test_reports_the_pinned_tzdata_package_version() -> None:
    # Zones load from the pinned tzdata package, not the OS database (2026e on the dev Mac).
    time = ZonedLocalTime(local=datetime(2023, 7, 1, 12, 0), zone="America/New_York")
    assert resolve(time, NEW_YORK).tzdata_version == importlib.metadata.version("tzdata")


@pytest.mark.parametrize("zone", ["Mars/Olympus_Mons", "../../etc/passwd", ""])
def test_unknown_zone(zone: str) -> None:
    with pytest.raises(UnknownTimeZone):
        resolve(ZonedLocalTime(local=datetime(2023, 7, 1, 12, 0), zone=zone), NEW_YORK)


# --- Zone rules: ambiguous (repeated) wall-clock times ------------------------------------
# A stated UTC offset picks one occurrence. Python's `fold` and an `is_dst` flag are not used.


def test_ambiguous_time_without_stated_offset_fails() -> None:
    time = ZonedLocalTime(local=datetime(2023, 11, 5, 1, 30), zone="America/New_York")
    with pytest.raises(AmbiguousLocalTime) as caught:
        resolve(time, NEW_YORK)
    assert caught.value.candidates == (hours(-4), hours(-5))  # earlier occurrence first


@pytest.mark.parametrize(
    ("stated", "expected_utc", "abbreviation"),
    [
        (hours(-4), datetime(2023, 11, 5, 5, 30, tzinfo=UTC), "EDT"),
        (hours(-5), datetime(2023, 11, 5, 6, 30, tzinfo=UTC), "EST"),
    ],
)
def test_ambiguous_time_resolved_by_stated_offset(
    stated: timedelta, expected_utc: datetime, abbreviation: str
) -> None:
    time = ZonedLocalTime(
        local=datetime(2023, 11, 5, 1, 30), zone="America/New_York", stated_utc_offset=stated
    )
    resolved = resolve(time, NEW_YORK)
    assert resolved.utc == expected_utc
    assert resolved.utc_offset == stated
    assert resolved.zone_abbreviation == abbreviation


def test_ambiguous_time_with_offset_that_is_neither_candidate_fails() -> None:
    time = ZonedLocalTime(
        local=datetime(2023, 11, 5, 1, 30), zone="America/New_York", stated_utc_offset=hours(-3)
    )
    with pytest.raises(StatedOffsetMismatch) as caught:
        resolve(time, NEW_YORK)
    assert caught.value.stated == hours(-3)
    assert caught.value.valid == (hours(-4), hours(-5))


def test_southern_hemisphere_ambiguous_time() -> None:
    time = ZonedLocalTime(local=datetime(2023, 4, 2, 2, 30), zone="Australia/Sydney")
    with pytest.raises(AmbiguousLocalTime) as caught:
        resolve(time, SYDNEY)
    assert caught.value.candidates == (hours(11), hours(10))


# --- Zone rules: nonexistent (skipped) wall-clock times -----------------------------------


@pytest.mark.parametrize(
    ("local", "zone", "place", "before", "after"),
    [
        (datetime(2023, 3, 12, 2, 30), "America/New_York", NEW_YORK, hours(-5), hours(-4)),
        (datetime(2023, 10, 1, 2, 30), "Australia/Sydney", SYDNEY, hours(10), hours(11)),
        # Samoa moved across the date line: all of 2011-12-30 was skipped.
        (datetime(2011, 12, 30, 12, 0), "Pacific/Apia", APIA, hours(-10), hours(14)),
    ],
)
def test_nonexistent_time_fails(
    local: datetime, zone: str, place: GeoPosition, before: timedelta, after: timedelta
) -> None:
    with pytest.raises(NonexistentLocalTime) as caught:
        resolve(ZonedLocalTime(local=local, zone=zone), place)
    assert (caught.value.offset_before, caught.value.offset_after) == (before, after)


def test_nonexistent_time_fails_even_with_stated_offset() -> None:
    # "02:30 at -05:00" is expressible, but as an OffsetLocalTime, not as a zone-rule time.
    time = ZonedLocalTime(
        local=datetime(2023, 3, 12, 2, 30), zone="America/New_York", stated_utc_offset=hours(-5)
    )
    with pytest.raises(NonexistentLocalTime):
        resolve(time, NEW_YORK)


def test_stated_offset_is_checked_for_unambiguous_times() -> None:
    # The record says EST, the zone rules say EDT: surface the conflict, trust neither.
    time = ZonedLocalTime(
        local=datetime(2023, 7, 1, 12, 0), zone="America/New_York", stated_utc_offset=hours(-5)
    )
    with pytest.raises(StatedOffsetMismatch) as caught:
        resolve(time, NEW_YORK)
    assert caught.value.valid == (hours(-4),)


# --- Zone rules before 1970 ---------------------------------------------------------------
# tzdb aims for accuracy since 1970, and some keys are links to another country's zone
# (Europe/Amsterdam -> Europe/Brussels), so earlier rules need explicit acceptance.


def test_pre_1970_zone_rules_need_acceptance() -> None:
    time = ZonedLocalTime(local=datetime(1925, 7, 1, 12, 0), zone="Europe/Amsterdam")
    with pytest.raises(Pre1970TzdbNotAccepted):
        resolve(time, AMSTERDAM)


def test_pre_1970_zone_rules_when_accepted() -> None:
    time = ZonedLocalTime(
        local=datetime(1925, 7, 1, 12, 0), zone="Europe/Amsterdam", accept_pre_1970_tzdb=True
    )
    resolved = resolve(time, AMSTERDAM)
    assert resolved.utc == datetime(1925, 7, 1, 11, 0, tzinfo=UTC)
    assert resolved.zone_abbreviation == "WEST"  # Brussels' history, via the link
    assert isinstance(resolved.input, ZonedLocalTime)
    assert resolved.input.accept_pre_1970_tzdb is True


def test_pre_1970_boundary() -> None:
    before = ZonedLocalTime(local=datetime(1969, 12, 31, 23, 59, 59), zone="America/New_York")
    with pytest.raises(Pre1970TzdbNotAccepted):
        resolve(before, NEW_YORK)
    at = ZonedLocalTime(local=datetime(1970, 1, 1, 0, 0, 0), zone="America/New_York")
    assert resolve(at, NEW_YORK).utc == datetime(1970, 1, 1, 5, 0, tzinfo=UTC)


@pytest.mark.parametrize("accept", [False, True])
def test_tzdb_local_mean_time_is_refused(accept: bool) -> None:
    # tzdb's LMT is the zone's reference-city value; accepting old rules doesn't change that.
    time = ZonedLocalTime(
        local=datetime(1880, 1, 1, 12, 0), zone="America/New_York", accept_pre_1970_tzdb=accept
    )
    with pytest.raises(TzdbLocalMeanTime) as caught:
        resolve(time, NEW_YORK)
    assert caught.value.tzdb_offset == -timedelta(hours=4, minutes=56, seconds=2)


def test_lmt_to_standard_time_switchover() -> None:
    # New York left LMT at 12:03:58 local on 1883-11-18 by setting clocks back to 12:00 EST,
    # so 12:00:00-12:03:57 happened twice: once as LMT, then as EST. Either reading may be
    # the real one, and one of them is reference-city LMT, so the time is refused.
    in_window = ZonedLocalTime(
        local=datetime(1883, 11, 18, 12, 2), zone="America/New_York", accept_pre_1970_tzdb=True
    )
    with pytest.raises(TzdbLocalMeanTime):
        resolve(in_window, NEW_YORK)
    after = ZonedLocalTime(
        local=datetime(1883, 11, 18, 12, 3, 58), zone="America/New_York", accept_pre_1970_tzdb=True
    )
    assert resolve(after, NEW_YORK).utc == datetime(1883, 11, 18, 17, 3, 58, tzinfo=UTC)


# --- Local mean time from the birthplace --------------------------------------------------


def test_local_mean_time_uses_birthplace_longitude() -> None:
    # 78.8784 W x 240 s/degree = 18930.816 s; tzdb would give New York's -4:56:02 instead.
    resolved = resolve(LocalMeanTime(local=datetime(1880, 1, 1, 12, 0)), BUFFALO)
    expected_offset = -timedelta(hours=5, minutes=15, seconds=30, milliseconds=816)
    assert resolved.utc_offset == expected_offset
    assert resolved.utc == datetime(1880, 1, 1, 17, 15, 30, 816000, tzinfo=UTC)
    assert resolved.offset_source is OffsetSource.LOCAL_MEAN_TIME
    assert resolved.zone_abbreviation is None
    assert resolved.tzdata_version is None


# --- Stated offsets -----------------------------------------------------------------------


def test_stated_offset_input() -> None:
    time = OffsetLocalTime(local=datetime(2023, 7, 1, 12, 0), utc_offset=hours(5.75))
    resolved = resolve(time, KATHMANDU)
    assert resolved.utc == datetime(2023, 7, 1, 6, 15, tzinfo=UTC)
    assert resolved.utc_offset == hours(5.75)
    assert resolved.offset_source is OffsetSource.STATED


# --- Births close together ----------------------------------------------------------------


def test_twins_across_fall_back_are_one_minute_apart() -> None:
    # The second wall-clock reading is 59 minutes *earlier*, but the births are 60 s apart.
    first = ZonedLocalTime(
        local=datetime(2023, 11, 5, 1, 59, 30), zone="America/New_York", stated_utc_offset=hours(-4)
    )
    second = ZonedLocalTime(
        local=datetime(2023, 11, 5, 1, 0, 30), zone="America/New_York", stated_utc_offset=hours(-5)
    )
    gap = resolve(second, NEW_YORK).utc - resolve(first, NEW_YORK).utc
    assert gap == timedelta(seconds=60)


def test_births_one_second_apart_stay_one_second_apart() -> None:
    a = ZonedLocalTime(local=datetime(2023, 7, 1, 12, 0, 0), zone="America/New_York")
    b = ZonedLocalTime(local=datetime(2023, 7, 1, 12, 0, 1), zone="America/New_York")
    assert resolve(b, NEW_YORK).utc - resolve(a, NEW_YORK).utc == timedelta(seconds=1)


# --- Inputs refused at construction -------------------------------------------------------
# Leap seconds (second 60) need no test: datetime cannot represent them, so these input
# types cannot carry one. Supporting them later would mean changing the input types.


def test_fold_attribute_is_refused() -> None:
    with pytest.raises(UnsupportedInput):
        ZonedLocalTime(local=datetime(2023, 11, 5, 1, 30, fold=1), zone="America/New_York")


def test_aware_local_time_is_refused() -> None:
    with pytest.raises(UnsupportedInput):
        OffsetLocalTime(local=datetime(2023, 7, 1, 12, tzinfo=UTC), utc_offset=hours(0))


def test_dates_before_gregorian_reform_are_refused() -> None:
    with pytest.raises(UnsupportedInput):
        OffsetLocalTime(local=datetime(1582, 10, 14, 12, 0), utc_offset=hours(0))
    # The first Gregorian day is accepted.
    OffsetLocalTime(local=datetime(1582, 10, 15, 12, 0), utc_offset=hours(0))


def test_utc_dates_before_gregorian_reform_are_refused() -> None:
    with pytest.raises(UnsupportedInput):
        UtcTime(utc=datetime(1582, 10, 14, 12, 0, tzinfo=UTC))
    UtcTime(utc=datetime(1582, 10, 15, 12, 0, tzinfo=UTC))


@pytest.mark.parametrize("offset", [hours(24), hours(-24), timedelta(days=30)])
def test_offsets_of_a_day_or_more_are_refused(offset: timedelta) -> None:
    # Python's own timezone type allows strictly less than 24 h; anything larger is not an
    # offset, and subtracting it could produce a nonsense instant or an OverflowError.
    with pytest.raises(UnsupportedInput):
        OffsetLocalTime(local=datetime(2023, 7, 1, 12, 0), utc_offset=offset)
    with pytest.raises(UnsupportedInput):
        ZonedLocalTime(
            local=datetime(2023, 7, 1, 12, 0), zone="America/New_York", stated_utc_offset=offset
        )


def test_offsets_just_under_a_day_are_accepted() -> None:
    almost = timedelta(hours=23, minutes=59, seconds=59)
    OffsetLocalTime(local=datetime(2023, 7, 1, 12, 0), utc_offset=almost)
    OffsetLocalTime(local=datetime(2023, 7, 1, 12, 0), utc_offset=-almost)


# --- Errors -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        AmbiguousLocalTime(
            datetime(2023, 11, 5, 1, 30), "America/New_York", (hours(-4), hours(-5))
        ),
        NonexistentLocalTime(
            datetime(2023, 3, 12, 2, 30), "America/New_York", hours(-5), hours(-4)
        ),
        StatedOffsetMismatch(datetime(2023, 7, 1, 12), "America/New_York", hours(-5), (hours(-4),)),
        TzdbLocalMeanTime(datetime(1880, 1, 1, 12), "America/New_York", hours(-4.9339)),
        Pre1970TzdbNotAccepted(datetime(1925, 7, 1, 12), "Europe/Amsterdam"),
        UnknownTimeZone("Mars/Olympus_Mons"),
        UnsupportedInput("fold=1 is not used; state the UTC offset instead"),
    ],
)
def test_errors_survive_pickling(error: CivilTimeError) -> None:
    # A future batch runner may return errors from worker processes, which pickles them.
    restored = pickle.loads(pickle.dumps(error))
    assert type(restored) is type(error)
    assert restored.args == error.args
    assert str(restored) == str(error)


# --- Zone data provenance -----------------------------------------------------------------


@pytest.fixture
def os_zone_database_that_disagrees(tmp_path: Path) -> Iterator[None]:
    """Point zoneinfo's OS search path at a database whose America/Chicago is Asia/Tokyo.

    Mutates zoneinfo's process-wide search path and cache; both are restored afterwards.
    America/Chicago is used by no other test, so no cached instance can mask the swap.
    """
    tokyo = importlib.resources.files("tzdata.zoneinfo").joinpath("Asia", "Tokyo").read_bytes()
    (tmp_path / "America").mkdir()
    (tmp_path / "America" / "Chicago").write_bytes(tokyo)
    zoneinfo.reset_tzpath(to=[str(tmp_path)])
    zoneinfo.ZoneInfo.clear_cache(only_keys=["America/Chicago"])
    try:
        yield
    finally:
        zoneinfo.reset_tzpath()
        zoneinfo.ZoneInfo.clear_cache(only_keys=["America/Chicago"])


@pytest.mark.usefixtures("os_zone_database_that_disagrees")
def test_zones_come_from_the_tzdata_package_not_the_os() -> None:
    july = datetime(2023, 7, 1, 12, 0)
    # Precondition: the OS path really does serve the wrong rules now.
    assert july.replace(tzinfo=zoneinfo.ZoneInfo("America/Chicago")).utcoffset() == hours(9)
    resolved = resolve(ZonedLocalTime(local=july, zone="America/Chicago"), CHICAGO)
    assert resolved.utc_offset == hours(-5)
    assert resolved.zone_abbreviation == "CDT"
