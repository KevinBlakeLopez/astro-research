"""Parans, proof of concept: every rise, set and meridian transit in a window, and the gaps.

`angular_events()` lists, for each chosen body and star, every rising, setting, upper and lower
meridian transit at one place within [start, end). `paran_gaps()` then gives, for every pair of
events of two different points, the time between them and the ARMC between them. There is no
orb and nothing marks a pair as a paran: how close counts is the researcher's rule.

This is not full paran support. It covers the body's actual position (ecliptic latitude
included, topocentric), as Swiss Ephemeris's rise_trans computes it. The zodiacal definition
(the body's degree on the Ascendant or MC) is not covered: rise_trans offers a zodiacal rising,
but its transits always use the body's actual right ascension.

Conventions, recorded as `PARAN_CONVENTIONS`:
- rise and set refer to the chosen point of the disc (stars are points) and the chosen
  refraction; without refraction the horizon is geometric. The caller names both;
- transits are at hour angle 0 and 180 degrees;
- every event is refined by a second search started 3 minutes before the first result. The
  first one can be off by up to 2.6 s (a star's transit) or 0.7 s (the Moon's); refined, events
  agree with ERFA within 7 ms (measured);
- a body or star that Swiss Ephemeris says does not rise or set (its -2) is listed in
  `no_rise_or_set`; circumpolar and never-rising cannot be told apart from that answer. Its
  transits are still listed;
- stars are named by catalogue designation (e.g. ",alLeo"), because names repeat in the
  catalogue. Swiss Ephemeris keeps the first catalogue it reads for the whole process; the
  environment check confirms it is the pinned one.
"""

from __future__ import annotations

import enum
import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # type-only imports: the adapter imports this module's types at runtime
    from astro_research.astronomical_time import AstronomicalTime
    from astro_research.ephemeris import Ephemeris, EphemerisProvenance
    from astro_research.geo import GeoPosition
    from astro_research.positions import Body

PARAN_CONVENTIONS = (
    "parans-v1: Swiss Ephemeris rise_trans, topocentric, the body's actual position (ecliptic "
    "latitude included); rise and set of the chosen disc point with the chosen refraction (none: "
    "geometric horizon); transits at hour angle 0 and 180; each event refined by a second search "
    "from 3 minutes before; events in [start, end); gaps in time and ARMC, no orb"
)
_REFINE_LEAD_DAYS = 3 / 1440
# After an event, search again from a minute later: the same event of the same point comes back
# no sooner than about 23 hours later.
_NEXT_SEARCH_DAYS = 1 / 1440
_ABSOLUTE_ZERO_C = -273.15


class InvalidHorizonConvention(ValueError):
    """The refraction's pressure or temperature is not physical."""


class InvalidParanRequest(ValueError):
    """The window is empty, or a point is requested twice."""


class StarNotFound(LookupError):
    """The star catalogue has no record for the designation."""


class HorizonEvent(enum.Enum):
    RISE = "rise"
    SET = "set"
    UPPER_TRANSIT = "upper_transit"
    LOWER_TRANSIT = "lower_transit"


class Disc(enum.Enum):
    """The point of the Sun's or Moon's disc that rises or sets; stars are points."""

    UPPER_LIMB = "upper_limb"
    CENTRE = "centre"
    LOWER_LIMB = "lower_limb"


@dataclass(frozen=True, kw_only=True)
class Refraction:
    pressure_hpa: float
    temperature_c: float

    def __post_init__(self) -> None:
        # Swiss Ephemeris reads a pressure of 0 as "estimate it from the elevation".
        if not (math.isfinite(self.pressure_hpa) and self.pressure_hpa > 0):
            raise InvalidHorizonConvention(f"pressure must be positive: {self.pressure_hpa!r}")
        if not (math.isfinite(self.temperature_c) and self.temperature_c > _ABSOLUTE_ZERO_C):
            raise InvalidHorizonConvention(f"temperature is not physical: {self.temperature_c!r}")


@dataclass(frozen=True, kw_only=True)
class HorizonConvention:
    disc: Disc
    refraction: Refraction | None  # None: the geometric horizon


@dataclass(frozen=True, kw_only=True)
class FixedStar:
    designation: str  # as the catalogue lists it, e.g. ",alLeo"


@dataclass(frozen=True, kw_only=True)
class AngularEvent:
    point: Body | FixedStar
    event: HorizonEvent
    time: AstronomicalTime
    armc: float  # degrees, at the event


@dataclass(frozen=True, kw_only=True)
class AngularEvents:
    place: GeoPosition
    start: AstronomicalTime
    end: AstronomicalTime
    horizon: HorizonConvention
    bodies: tuple[Body, ...]
    stars: tuple[FixedStar, ...]
    events: tuple[AngularEvent, ...]  # in time order
    no_rise_or_set: tuple[tuple[Body | FixedStar, HorizonEvent], ...]  # Swiss Ephemeris's -2
    star_names: tuple[tuple[FixedStar, str], ...]  # e.g. (FixedStar(",alLeo"), "Regulus,alLeo")
    ephemeris: EphemerisProvenance
    conventions: str


@dataclass(frozen=True, kw_only=True)
class ParanGap:
    first: AngularEvent
    second: AngularEvent  # not earlier than `first`
    seconds: float  # second - first, in TT
    armc: float  # second's ARMC - first's, wrapped into (-180, 180]


def angular_events(
    eph: Ephemeris,
    place: GeoPosition,
    *,
    start: AstronomicalTime,
    end: AstronomicalTime,
    bodies: Iterable[Body],
    stars: Iterable[FixedStar],
    horizon: HorizonConvention,
) -> AngularEvents:
    """Every rise, set and transit of `bodies` and `stars` at `place` in [start, end)."""
    if end.jd_tt <= start.jd_tt:
        raise InvalidParanRequest("the window must end after it starts")
    bodies, stars = tuple(bodies), tuple(stars)
    if len(set(bodies)) != len(bodies) or len(set(stars)) != len(stars):
        raise InvalidParanRequest(f"points must be distinct: {bodies}, {stars}")
    eph.angles(start, place)  # the angles, and the ARMC, are refused at the poles
    # Swiss Ephemeris would compute events outside the data files with Moshier and say nothing.
    eph.apparent_vectors(start.jd_tt)
    eph.apparent_vectors(end.jd_tt)
    star_names = tuple((star, eph.star_name(star)) for star in stars)
    found: list[AngularEvent] = []
    missing: list[tuple[Body | FixedStar, HorizonEvent]] = []
    points: tuple[Body | FixedStar, ...] = (*bodies, *stars)
    for point in points:
        for event in HorizonEvent:
            _collect(eph, place, start, end, horizon, point, event, found, missing)
    found.sort(key=lambda e: e.time.jd_tt)
    return AngularEvents(
        place=place,
        start=start,
        end=end,
        horizon=horizon,
        bodies=bodies,
        stars=stars,
        events=tuple(found),
        no_rise_or_set=tuple(missing),
        star_names=star_names,
        ephemeris=eph.provenance,
        conventions=PARAN_CONVENTIONS,
    )


def paran_gaps(events: AngularEvents) -> tuple[ParanGap, ...]:
    """Every pair of events of two different points, earlier event first."""
    ordered = events.events
    return tuple(
        ParanGap(
            first=a,
            second=b,
            seconds=(b.time.jd_tt - a.time.jd_tt) * 86400,
            armc=_wrap(b.armc - a.armc),
        )
        for i, a in enumerate(ordered)
        for b in ordered[i + 1 :]
        if a.point != b.point
    )


def _collect(
    eph: Ephemeris,
    place: GeoPosition,
    start: AstronomicalTime,
    end: AstronomicalTime,
    horizon: HorizonConvention,
    point: Body | FixedStar,
    event: HorizonEvent,
    found: list[AngularEvent],
    missing: list[tuple[Body | FixedStar, HorizonEvent]],
) -> None:
    # Nothing at or after the end is converted to a time: under UT1_FROM_IERS a search can run on
    # past the table's last day, where there is no UT1.
    search_from = start.jd_ut1
    while search_from < end.jd_ut1:
        first = eph.rise_trans(point, event, search_from, place, horizon, start.time_scale)
        if first is None:
            if (point, event) not in missing:
                missing.append((point, event))
            # No rising or setting this cycle; carry on after the next upper transit.
            transit = eph.rise_trans(
                point, HorizonEvent.UPPER_TRANSIT, search_from, place, horizon, start.time_scale
            )
            if transit is None or transit >= end.jd_ut1:
                return
            search_from = transit + _NEXT_SEARCH_DAYS
            continue
        if first - _REFINE_LEAD_DAYS >= end.jd_ut1:
            return  # the refined search starts there, so its event is past the end too
        refined = eph.rise_trans(
            point, event, first - _REFINE_LEAD_DAYS, place, horizon, start.time_scale
        )
        jd_ut1 = first if refined is None else refined
        if jd_ut1 >= end.jd_ut1:
            return
        time = eph.time_from_ut1(jd_ut1, start.time_scale)
        if time.jd_tt >= end.jd_tt:
            return
        if time.jd_tt >= start.jd_tt:
            armc = eph.angles(time, place).armc
            found.append(AngularEvent(point=point, event=event, time=time, armc=armc))
        search_from = jd_ut1 + _NEXT_SEARCH_DAYS


def _wrap(degrees: float) -> float:
    """Into (-180, 180]. Both ARMCs lie within [0, 360), so one turn is enough."""
    if degrees > 180.0:
        return degrees - 360.0
    if degrees <= -180.0:
        return degrees + 360.0
    return degrees
