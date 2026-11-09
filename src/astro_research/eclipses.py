"""Eclipses: the nearest solar and lunar eclipses before and after an instant.

Swiss Ephemeris finds each eclipse and gives its type, central flag, magnitudes, saros number
and (solar) the place of greatest eclipse. Its own instant of greatest eclipse uses a different
definition from NASA's and, for lunar eclipses, is found coarsely (up to 9.6 s and 4 s away,
measured). So the instant is refined here to NASA's definition, on Swiss Ephemeris's apparent
geocentric positions:
- solar: Earth's centre is closest to the shadow axis (the line through the Moon, away from the
  Sun). This reproduces NASA's canon within 0.55 s for 1901-2100 (NASA rounds to 1 s);
- lunar: the Moon is closest, in geocentric angle, to the point opposite the Sun. This
  reproduces the canon within 2.75 s; beyond rounding, the cause is not investigated.
The minimum is found to about 0.1 s; the quantities are not resolved below ~10 ms (measured).

Conventions, recorded as `ECLIPSE_CONVENTIONS`:
- "before" and "after" compare greatest eclipse with the given instant, strictly, in TT;
- which types count is the caller's choice (e.g. leaving out penumbral lunar eclipses);
- the syzygy is the exact new or full moon: apparent geocentric longitudes equal or 180 degrees
  apart. It is up to 18 minutes from greatest eclipse (measured);
- a lunar eclipse's magnitude is umbral, 0 for a penumbral eclipse (Swiss Ephemeris's value,
  where NASA prints a negative one); its penumbral magnitude is kept separately;
- instants are expressed as UTC readings under the caller's time scale, rounded to the
  microsecond; their TT does not depend on the time scale;
- eclipses outside the ephemeris files are refused, because Swiss Ephemeris would compute them
  with its Moshier model without saying so.
"""

from __future__ import annotations

import enum
import math
from collections.abc import Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING

from astro_research.positions import Body

if TYPE_CHECKING:  # type-only imports: the adapter imports this module's types at runtime
    from astro_research.astronomical_time import AstronomicalTime, TimeScale
    from astro_research.ephemeris import Ephemeris, EphemerisProvenance
    from astro_research.geo import GeoPosition
    from astro_research.positions import BodyPosition

ECLIPSE_CONVENTIONS = (
    "eclipses-v1: found and classified by Swiss Ephemeris (type, central, magnitudes, saros, "
    "place); greatest eclipse refined to NASA's definition on apparent geocentric positions "
    "(solar: Earth's centre closest to the shadow axis; lunar: Moon closest in angle to the "
    "anti-Sun point), to about 0.1 s; syzygy: apparent longitudes equal or opposite; before and "
    "after strictly, in TT; lunar magnitude umbral, 0 when penumbral"
)
# Swiss Ephemeris's estimate is within seconds of the refined instant (under 10 s measured), and
# eclipses of one kind are at least a lunation apart, so a day either way loses none.
_SEARCH_MARGIN_DAYS = 1.0
# The refinement searches +-29 minutes around the estimate, down to 0.17 ms.
_REFINE_HALF_WIDTH_DAYS = 0.02
_REFINE_TOLERANCE_DAYS = 2e-9
# Newton's method from greatest eclipse (at most ~18 minutes from the syzygy) converges
# quadratically: a few steps reach the Julian-date resolution.
_SYZYGY_STEPS = 6
_GOLDEN = (math.sqrt(5) - 1) / 2


class EclipseKind(enum.Enum):
    SOLAR = "solar"
    LUNAR = "lunar"


class EclipseType(enum.Enum):
    TOTAL = "total"
    ANNULAR = "annular"
    HYBRID = "hybrid"  # annular-total
    PARTIAL = "partial"
    PENUMBRAL = "penumbral"


SOLAR_TYPES = frozenset(
    {EclipseType.TOTAL, EclipseType.ANNULAR, EclipseType.HYBRID, EclipseType.PARTIAL}
)
LUNAR_TYPES = frozenset({EclipseType.TOTAL, EclipseType.PARTIAL, EclipseType.PENUMBRAL})


class InvalidEclipseTypes(ValueError):
    """No eclipse types were chosen, or a type does not exist for that kind of eclipse."""


@dataclass(frozen=True, kw_only=True)
class EclipseCandidate:
    """An eclipse as Swiss Ephemeris's search finds it."""

    kind: EclipseKind
    type: EclipseType
    central: bool | None  # solar only
    estimate_jd_ut1: float  # Swiss Ephemeris's own greatest eclipse
    estimate_jd_tt: float


@dataclass(frozen=True, kw_only=True)
class EclipseAttributes:
    magnitude: float  # solar: NASA's magnitude; lunar: umbral, 0 when penumbral
    penumbral_magnitude: float | None  # lunar only
    saros_series: int | None  # None if Swiss Ephemeris has no saros number
    saros_member: int | None
    place_of_greatest: GeoPosition | None  # solar only


@dataclass(frozen=True, kw_only=True)
class EclipseInstant:
    time: AstronomicalTime
    sun: BodyPosition
    moon: BodyPosition


@dataclass(frozen=True, kw_only=True)
class Eclipse:
    kind: EclipseKind
    type: EclipseType
    central: bool | None  # solar only
    greatest: EclipseInstant  # NASA's definition, refined
    syzygy: EclipseInstant  # the exact new or full moon
    magnitude: float  # solar: NASA's magnitude; lunar: umbral, 0 when penumbral
    penumbral_magnitude: float | None  # lunar only
    saros_series: int | None
    saros_member: int | None
    place_of_greatest: GeoPosition | None  # solar only


@dataclass(frozen=True, kw_only=True)
class NearestEclipses:
    birth: AstronomicalTime
    solar_types: frozenset[EclipseType]
    lunar_types: frozenset[EclipseType]
    solar_before: Eclipse
    solar_after: Eclipse
    lunar_before: Eclipse
    lunar_after: Eclipse
    ephemeris: EphemerisProvenance
    conventions: str


def nearest_eclipses(
    eph: Ephemeris,
    birth: AstronomicalTime,
    *,
    solar_types: Collection[EclipseType],
    lunar_types: Collection[EclipseType],
) -> NearestEclipses:
    """The nearest solar and lunar eclipses of the chosen types before and after `birth`."""
    solar = _checked_types(EclipseKind.SOLAR, solar_types)
    lunar = _checked_types(EclipseKind.LUNAR, lunar_types)
    return NearestEclipses(
        birth=birth,
        solar_types=solar,
        lunar_types=lunar,
        solar_before=previous_eclipse(eph, birth, EclipseKind.SOLAR, solar),
        solar_after=next_eclipse(eph, birth, EclipseKind.SOLAR, solar),
        lunar_before=previous_eclipse(eph, birth, EclipseKind.LUNAR, lunar),
        lunar_after=next_eclipse(eph, birth, EclipseKind.LUNAR, lunar),
        ephemeris=eph.provenance,
        conventions=ECLIPSE_CONVENTIONS,
    )


def next_eclipse(
    eph: Ephemeris, after: AstronomicalTime, kind: EclipseKind, types: Collection[EclipseType]
) -> Eclipse:
    """The first eclipse of `kind` and `types` whose greatest eclipse is after `after`."""
    return _search(eph, after, kind, _checked_types(kind, types), backward=False)


def previous_eclipse(
    eph: Ephemeris, before: AstronomicalTime, kind: EclipseKind, types: Collection[EclipseType]
) -> Eclipse:
    """The last eclipse of `kind` and `types` whose greatest eclipse is before `before`."""
    return _search(eph, before, kind, _checked_types(kind, types), backward=True)


def _checked_types(kind: EclipseKind, types: Collection[EclipseType]) -> frozenset[EclipseType]:
    chosen = frozenset(types)
    allowed = SOLAR_TYPES if kind is EclipseKind.SOLAR else LUNAR_TYPES
    if not chosen or not chosen <= allowed:
        raise InvalidEclipseTypes(
            f"{kind.value} eclipse types must be a non-empty choice from "
            f"{sorted(t.value for t in allowed)}, not {sorted(t.value for t in chosen)}"
        )
    return chosen


def _search(
    eph: Ephemeris,
    time: AstronomicalTime,
    kind: EclipseKind,
    types: frozenset[EclipseType],
    *,
    backward: bool,
) -> Eclipse:
    step = -_SEARCH_MARGIN_DAYS if backward else _SEARCH_MARGIN_DAYS
    start = time.jd_ut1 - step  # start a day beyond the instant: the refined one decides
    while True:
        candidate = eph.eclipse_search(kind, start, backward=backward)
        start = candidate.estimate_jd_ut1 + step
        # Refining every candidate also refuses one outside the data files, which ends the search.
        greatest = _refine(eph, kind, candidate.estimate_jd_tt)
        beyond = greatest < time.jd_tt if backward else greatest > time.jd_tt
        if beyond and candidate.type in types:
            return _eclipse(eph, candidate, greatest, time.time_scale)


def _refine(eph: Ephemeris, kind: EclipseKind, estimate_jd_tt: float) -> float:
    """Golden-section minimum of the defining quantity around Swiss Ephemeris's estimate."""

    def quantity(jd_tt: float) -> float:
        sun, moon = eph.apparent_vectors(jd_tt)
        if kind is EclipseKind.SOLAR:
            axis = (moon[0] - sun[0], moon[1] - sun[1], moon[2] - sun[2])
            return _norm(_cross(moon, axis)) / _norm(axis)
        antisun = (-sun[0], -sun[1], -sun[2])
        return math.atan2(_norm(_cross(moon, antisun)), _dot(moon, antisun))

    a, b = estimate_jd_tt - _REFINE_HALF_WIDTH_DAYS, estimate_jd_tt + _REFINE_HALF_WIDTH_DAYS
    c, d = b - _GOLDEN * (b - a), a + _GOLDEN * (b - a)
    fc, fd = quantity(c), quantity(d)
    while b - a > _REFINE_TOLERANCE_DAYS:
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - _GOLDEN * (b - a)
            fc = quantity(c)
        else:
            a, c, fc = c, d, fd
            d = a + _GOLDEN * (b - a)
            fd = quantity(d)
    return (a + b) / 2


def _syzygy(eph: Ephemeris, kind: EclipseKind, start_jd_tt: float) -> float:
    target = 0.0 if kind is EclipseKind.SOLAR else 180.0
    jd_tt = start_jd_tt
    for _ in range(_SYZYGY_STEPS):
        sun, sun_speed, moon, moon_speed = eph.apparent_longitudes(jd_tt)
        jd_tt -= math.remainder(moon - sun - target, 360.0) / (moon_speed - sun_speed)
    return jd_tt


def _eclipse(
    eph: Ephemeris, candidate: EclipseCandidate, greatest_jd_tt: float, time_scale: TimeScale
) -> Eclipse:
    greatest = _instant(eph, greatest_jd_tt, time_scale)
    attributes = eph.eclipse_attributes(candidate.kind, greatest.time)
    syzygy_jd_tt = _syzygy(eph, candidate.kind, greatest_jd_tt)
    return Eclipse(
        kind=candidate.kind,
        type=candidate.type,
        central=candidate.central,
        greatest=greatest,
        syzygy=_instant(eph, syzygy_jd_tt, time_scale),
        magnitude=attributes.magnitude,
        penumbral_magnitude=attributes.penumbral_magnitude,
        saros_series=attributes.saros_series,
        saros_member=attributes.saros_member,
        place_of_greatest=attributes.place_of_greatest,
    )


def _instant(eph: Ephemeris, jd_tt: float, time_scale: TimeScale) -> EclipseInstant:
    time = eph.time_from_tt(jd_tt, time_scale)
    positions = eph.positions(time, (Body.SUN, Body.MOON))
    return EclipseInstant(time=time, sun=positions[Body.SUN], moon=positions[Body.MOON])


def _cross(
    a: tuple[float, float, float], b: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a: tuple[float, float, float]) -> float:
    return math.sqrt(_dot(a, a))
