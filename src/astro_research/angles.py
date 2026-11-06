"""The chart's angles: Ascendant, MC, Descendant, IC and Vertex, with ARMC. Domain types only.

Conventions, recorded in every result as `ANGLE_CONVENTIONS`:
- tropical, on the true ecliptic of date; the angles follow the Earth's rotation, so they are
  computed from UT1 (under `TimeScale.CLOCK_AS_UT1` the clock reading stands in for it);
- Swiss Ephemeris's sidereal time: the IERS 2010 model from 1850-01-01 to 2050-01-01 and its
  long-term model outside, with observed steps of 97 mas and 1.9 arcsec at those two dates;
- the Ascendant is always the eastern rising point of the ecliptic; beyond the polar circles
  the MC stays on the upper meridian even when it is below the horizon;
- the Vertex is the ecliptic point due west, always in the western hemisphere; on the equator
  it is an equinox point (0 Aries or 0 Libra) and does not move;
- the Descendant and IC are the Ascendant and MC plus 180 degrees.

Angles are undefined at the geographic poles and refused there. Values are computed by
`Ephemeris.angles()` in the adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # type-only imports keep this module free of the adapter at runtime
    from astro_research.astronomical_time import AstronomicalTime
    from astro_research.ephemeris import EphemerisProvenance
    from astro_research.geo import GeoPosition

# Swiss Ephemeris's sidereal-time model switches (SIDT_LTERM_T0 and _T1 in swephlib.c, 2.10.03):
# the long-term model at jd_ut1 <= 2396758.5 (1850-01-01) and jd_ut1 >= 2469807.5 (2050-01-01),
# IERS 2010 strictly between. Observed at those instants: the boundary itself is long-term.
SIDEREAL_TIME_SWITCHES_JD_UT1 = (2396758.5, 2469807.5)

ANGLE_CONVENTIONS = (
    "angles-v1: tropical, true ecliptic of date, from UT1; Swiss Ephemeris sidereal time (IERS "
    "2010 for 1850-01-01..2050-01-01, its long-term model outside); Ascendant always the eastern "
    "rising point, MC on the upper meridian even below the horizon, Vertex always due west; "
    "Descendant and IC = Ascendant and MC + 180"
)


class AnglesUndefined(ValueError):
    """The angles are undefined at the geographic poles."""


@dataclass(frozen=True, kw_only=True)
class Angles:
    time: AstronomicalTime
    place: GeoPosition
    ephemeris: EphemerisProvenance
    conventions: str
    armc: float  # right ascension of the MC, i.e. local apparent sidereal time, in degrees
    armc_speed: float  # degrees per day
    ascendant: float
    ascendant_speed: float
    midheaven: float
    midheaven_speed: float
    descendant: float  # ascendant + 180
    imum_coeli: float  # midheaven + 180
    vertex: float
    vertex_speed: float
    true_obliquity: float  # degrees, of date; gives the angles' declinations
    beyond_polar_circle: bool  # |latitude| > 90 - true obliquity of date
