"""Resolve a recorded civil (clock) time to a UTC instant.

A birth time arrives in one of four forms, each its own input type:

- `UtcTime`: already a UTC instant.
- `ZonedLocalTime`: a wall-clock reading plus an IANA zone; the offset comes from tzdb rules.
- `OffsetLocalTime`: a wall-clock reading plus the UTC offset the source states.
- `LocalMeanTime`: a wall-clock reading in local mean time at the birthplace.

`resolve()` never guesses. Repeated or skipped wall-clock times, offsets that contradict the
zone rules, tzdb's reference-city local mean time, and tzdb rules before 1970 (unless accepted)
all raise a `CivilTimeError` subclass carrying what is needed to correct the record.

Zones are loaded from the pinned `tzdata` package, never from the operating system's database,
and the package version is recorded in every tzdb-based result.
"""

import enum
import functools
import importlib.metadata
import importlib.resources
import zoneinfo
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import assert_never

from astro_research.geo import GeoPosition

GREGORIAN_START = datetime(1582, 10, 15)
TZDB_RELIABLE_FROM = datetime(1970, 1, 1)
SECONDS_PER_DEGREE_OF_LONGITUDE = 240  # 86 400 s per 360 degrees
TZDATA_VERSION = importlib.metadata.version("tzdata")
_TZDATA_KEYS = frozenset(importlib.resources.files("tzdata").joinpath("zones").read_text().split())


# --- Errors ---------------------------------------------------------------------------------
# Each error passes all of its fields to BaseException.__init__ so that `args` can rebuild it:
# pickling (e.g. returning an error from a worker process) calls `cls(*args)`.


class CivilTimeError(ValueError):
    """Base class for civil-time input and resolution errors."""


class UnsupportedInput(CivilTimeError):
    """The input cannot be represented faithfully; raised when the input is constructed."""


class UnknownTimeZone(CivilTimeError):
    def __init__(self, zone: str) -> None:
        super().__init__(zone)
        self.zone = zone

    def __str__(self) -> str:
        return f"{self.zone!r} is not a time zone in tzdata {TZDATA_VERSION}"


class AmbiguousLocalTime(CivilTimeError):
    """The wall-clock time occurs twice. State the UTC offset to choose one occurrence."""

    def __init__(self, local: datetime, zone: str, candidates: tuple[timedelta, timedelta]) -> None:
        super().__init__(local, zone, candidates)
        self.local = local
        self.zone = zone
        self.candidates = candidates  # earlier occurrence first

    def __str__(self) -> str:
        first, second = (_format_offset(c) for c in self.candidates)
        return f"{self.local.isoformat()} occurs twice in {self.zone}: at {first} and at {second}"


class NonexistentLocalTime(CivilTimeError):
    """The wall-clock time was skipped by a transition. Use OffsetLocalTime if it was real."""

    def __init__(
        self, local: datetime, zone: str, offset_before: timedelta, offset_after: timedelta
    ) -> None:
        super().__init__(local, zone, offset_before, offset_after)
        self.local = local
        self.zone = zone
        self.offset_before = offset_before
        self.offset_after = offset_after

    def __str__(self) -> str:
        return (
            f"{self.local.isoformat()} does not exist in {self.zone}: the offset changed from "
            f"{_format_offset(self.offset_before)} to {_format_offset(self.offset_after)}"
        )


class StatedOffsetMismatch(CivilTimeError):
    """The UTC offset stated by the source is not one the zone rules allow at that time."""

    def __init__(
        self, local: datetime, zone: str, stated: timedelta, valid: tuple[timedelta, ...]
    ) -> None:
        super().__init__(local, zone, stated, valid)
        self.local = local
        self.zone = zone
        self.stated = stated
        self.valid = valid

    def __str__(self) -> str:
        allowed = " or ".join(_format_offset(v) for v in self.valid)
        return (
            f"stated offset {_format_offset(self.stated)} for {self.local.isoformat()} in "
            f"{self.zone} contradicts the zone rules, which give {allowed}"
        )


class TzdbLocalMeanTime(CivilTimeError):
    """tzdb gives local mean time, which is for the zone's reference city, not the birthplace."""

    def __init__(self, local: datetime, zone: str, tzdb_offset: timedelta) -> None:
        super().__init__(local, zone, tzdb_offset)
        self.local = local
        self.zone = zone
        self.tzdb_offset = tzdb_offset

    def __str__(self) -> str:
        return (
            f"{self.zone} at {self.local.isoformat()} is tzdb local mean time "
            f"({_format_offset(self.tzdb_offset)}, the zone's reference city); "
            "use LocalMeanTime for the birthplace or OffsetLocalTime for a stated offset"
        )


class Pre1970TzdbNotAccepted(CivilTimeError):
    """tzdb rules before 1970 are used only when the input accepts them explicitly."""

    def __init__(self, local: datetime, zone: str) -> None:
        super().__init__(local, zone)
        self.local = local
        self.zone = zone

    def __str__(self) -> str:
        return (
            f"{self.local.isoformat()} in {self.zone} is before 1970, where tzdb is not "
            "authoritative; set accept_pre_1970_tzdb=True or use OffsetLocalTime"
        )


def _format_offset(offset: timedelta) -> str:
    sign = "-" if offset < timedelta(0) else "+"
    return f"UTC{sign}{abs(offset)}"


# --- Inputs ---------------------------------------------------------------------------------


def _require_plain_gregorian(moment: datetime) -> None:
    """Shared checks for wall-clock readings."""
    if moment.tzinfo is not None:
        raise UnsupportedInput(f"{moment.isoformat()} must be a plain wall-clock reading")
    if moment.fold != 0:
        # fold is invisible in repr and easy to set by accident; a stated offset is explicit.
        raise UnsupportedInput("fold=1 is not used; state the UTC offset instead")
    if moment < GREGORIAN_START:
        raise UnsupportedInput(f"{moment.date()} is before the Gregorian calendar (1582-10-15)")


def _require_offset_under_a_day(offset: timedelta) -> None:
    # The same limit as datetime.timezone; real UTC offsets stay far inside it.
    if not -timedelta(hours=24) < offset < timedelta(hours=24):
        raise UnsupportedInput(f"UTC offset {_format_offset(offset)} is not within 24 hours")


@dataclass(frozen=True)
class UtcTime:
    utc: datetime

    def __post_init__(self) -> None:
        offset = self.utc.utcoffset()
        if offset is None:
            raise UnsupportedInput(f"{self.utc.isoformat()} has no time zone; UTC is required")
        if offset != timedelta(0):
            raise UnsupportedInput(
                f"{self.utc.isoformat()} is not UTC; use OffsetLocalTime for a stated offset"
            )
        if self.utc.replace(tzinfo=None) < GREGORIAN_START:
            raise UnsupportedInput(f"{self.utc.date()} is before the Gregorian calendar")


@dataclass(frozen=True)
class ZonedLocalTime:
    local: datetime
    zone: str
    stated_utc_offset: timedelta | None = None  # required if ambiguous; checked if given
    accept_pre_1970_tzdb: bool = False

    def __post_init__(self) -> None:
        _require_plain_gregorian(self.local)
        if self.stated_utc_offset is not None:
            _require_offset_under_a_day(self.stated_utc_offset)


@dataclass(frozen=True)
class OffsetLocalTime:
    local: datetime
    utc_offset: timedelta  # as stated by the source

    def __post_init__(self) -> None:
        _require_plain_gregorian(self.local)
        _require_offset_under_a_day(self.utc_offset)


@dataclass(frozen=True)
class LocalMeanTime:
    """Local mean time at the birthplace: offset = longitude x 4 minutes per degree."""

    local: datetime

    def __post_init__(self) -> None:
        _require_plain_gregorian(self.local)


type CivilTime = UtcTime | ZonedLocalTime | OffsetLocalTime | LocalMeanTime


# --- Result ---------------------------------------------------------------------------------


class OffsetSource(enum.Enum):
    UTC_GIVEN = "utc_given"
    TZDB = "tzdb"
    STATED = "stated"
    LOCAL_MEAN_TIME = "local_mean_time"


@dataclass(frozen=True, kw_only=True)  # several fields share a type; positional args could swap
class ResolvedInstant:
    input: CivilTime  # kept exactly as given
    utc: datetime  # aware, UTC
    utc_offset: timedelta  # local = utc + utc_offset
    offset_source: OffsetSource
    zone_abbreviation: str | None  # tzdb only
    tzdata_version: str | None  # tzdb only


# --- Resolution -----------------------------------------------------------------------------


def resolve(time: CivilTime, place: GeoPosition) -> ResolvedInstant:
    match time:
        case UtcTime():
            return ResolvedInstant(
                input=time,
                utc=time.utc.astimezone(UTC),
                utc_offset=timedelta(0),
                offset_source=OffsetSource.UTC_GIVEN,
                zone_abbreviation=None,
                tzdata_version=None,
            )
        case OffsetLocalTime():
            return _from_offset(time, time.local, time.utc_offset, OffsetSource.STATED)
        case LocalMeanTime():
            offset = timedelta(seconds=place.longitude * SECONDS_PER_DEGREE_OF_LONGITUDE)
            return _from_offset(time, time.local, offset, OffsetSource.LOCAL_MEAN_TIME)
        case ZonedLocalTime():
            return _resolve_zoned(time)
        case _:
            assert_never(time)


def _from_offset(
    time: CivilTime, local: datetime, offset: timedelta, source: OffsetSource
) -> ResolvedInstant:
    return ResolvedInstant(
        input=time,
        utc=(local - offset).replace(tzinfo=UTC),
        utc_offset=offset,
        offset_source=source,
        zone_abbreviation=None,
        tzdata_version=None,
    )


def _resolve_zoned(time: ZonedLocalTime) -> ResolvedInstant:
    zone = _load_zone(time.zone)
    # PEP 495: for a repeated time fold=0 is the earlier occurrence; for a skipped time fold=0
    # applies the offset from before the transition and fold=1 the offset after it.
    earlier = time.local.replace(tzinfo=zone)
    later = time.local.replace(tzinfo=zone, fold=1)

    for candidate in (earlier, later):
        if candidate.tzname() == "LMT":  # tzdb's abbreviation for local mean time
            raise TzdbLocalMeanTime(time.local, time.zone, _utc_offset(candidate))
    if time.local < TZDB_RELIABLE_FROM and not time.accept_pre_1970_tzdb:
        raise Pre1970TzdbNotAccepted(time.local, time.zone)

    if _utc_offset(earlier) == _utc_offset(later):
        candidates: tuple[datetime, ...] = (earlier,)
    elif _survives_round_trip(earlier, zone):
        candidates = (earlier, later)  # the wall-clock time occurs twice
    else:
        raise NonexistentLocalTime(time.local, time.zone, _utc_offset(earlier), _utc_offset(later))

    chosen = _choose(time, candidates)
    return ResolvedInstant(
        input=time,
        utc=chosen.astimezone(UTC),
        utc_offset=_utc_offset(chosen),
        offset_source=OffsetSource.TZDB,
        zone_abbreviation=chosen.tzname(),
        tzdata_version=TZDATA_VERSION,
    )


def _choose(time: ZonedLocalTime, candidates: tuple[datetime, ...]) -> datetime:
    offsets = tuple(_utc_offset(c) for c in candidates)
    if time.stated_utc_offset is None:
        if len(candidates) == 1:
            return candidates[0]
        raise AmbiguousLocalTime(time.local, time.zone, (offsets[0], offsets[1]))
    for candidate, offset in zip(candidates, offsets, strict=True):
        if offset == time.stated_utc_offset:
            return candidate
    raise StatedOffsetMismatch(time.local, time.zone, time.stated_utc_offset, offsets)


def _survives_round_trip(moment: datetime, zone: zoneinfo.ZoneInfo) -> bool:
    """A skipped wall-clock time comes back shifted after converting to UTC and back."""
    # The zone is passed explicitly: astimezone(None) would silently use the machine's zone.
    back = moment.astimezone(UTC).astimezone(zone)
    return back.replace(tzinfo=None) == moment.replace(tzinfo=None)


def _utc_offset(moment: datetime) -> timedelta:
    offset = moment.utcoffset()
    if offset is None:  # cannot happen for a ZoneInfo-aware datetime; narrows the type
        raise RuntimeError(f"{moment!r} has no UTC offset")
    return offset


@functools.cache
def _load_zone(key: str) -> zoneinfo.ZoneInfo:
    """Load a zone from the pinned tzdata package, never from the OS database."""
    if key not in _TZDATA_KEYS:  # also rejects path-like keys before any file access
        raise UnknownTimeZone(key)
    resource = importlib.resources.files("tzdata.zoneinfo").joinpath(*key.split("/"))
    with resource.open("rb") as data:
        return zoneinfo.ZoneInfo.from_file(data, key=key)
