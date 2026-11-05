"""Positions of the Sun, Moon and planets: the domain types.

Conventions, fixed in Phase 1 and recorded in every result as `POSITION_CONVENTIONS`:
- geocentric and apparent: light-time, aberration and light deflection by the Sun applied;
- tropical, on the true ecliptic and the true equinox of date (nutation included);
- Jupiter through Pluto are their system barycenters, as JPL's DE and Swiss Ephemeris give them
  (observed: Swiss Ephemeris's Pluto matches Horizons' barycenter to 0.6 mas, its body center
  differs by 76 mas).

Angles are in degrees, distances in AU and speeds per day, unrounded as Swiss Ephemeris returns
them. Values are computed by `Ephemeris.positions()` in the adapter; this module only defines them.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # type-only imports keep this module free of the adapter at runtime
    from astro_research.astronomical_time import AstronomicalTime
    from astro_research.ephemeris import EphemerisProvenance

POSITION_CONVENTIONS = (
    "positions-v1: geocentric, apparent (light-time, aberration, solar light deflection); "
    "tropical, true ecliptic and true equinox of date; Jupiter-Pluto as system barycenters"
)


class Body(enum.Enum):
    SUN = "sun"
    MOON = "moon"
    MERCURY = "mercury"
    VENUS = "venus"
    MARS = "mars"
    JUPITER = "jupiter"
    SATURN = "saturn"
    URANUS = "uranus"
    NEPTUNE = "neptune"
    PLUTO = "pluto"


@dataclass(frozen=True, kw_only=True)
class BodyPosition:
    body: Body
    longitude: float  # true ecliptic and equinox of date
    latitude: float
    distance_au: float
    longitude_speed: float  # degrees per day
    latitude_speed: float
    distance_speed: float  # AU per day
    right_ascension: float  # true equator and equinox of date
    declination: float


@dataclass(frozen=True, kw_only=True)
class Positions:
    time: AstronomicalTime
    ephemeris: EphemerisProvenance
    conventions: str
    bodies: tuple[BodyPosition, ...]

    def __getitem__(self, body: Body) -> BodyPosition:
        for position in self.bodies:
            if position.body is body:
                return position
        raise KeyError(body)
