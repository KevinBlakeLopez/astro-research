"""Positions of the Sun, Moon, planets and lunar points: the domain types.

Conventions, fixed in Phase 1 and recorded in every result as `POSITION_CONVENTIONS`:
- geocentric and apparent: light-time, aberration and light deflection by the Sun applied;
- tropical, on the true ecliptic and the true equinox of date (nutation included);
- Jupiter through Pluto are their system barycenters, as JPL's DE and Swiss Ephemeris give them
  (observed: Swiss Ephemeris's Pluto matches Horizons' barycenter to 0.6 mas, its body center
  differs by 76 mas).

Lunar points (Swiss Ephemeris manual §2.2):
- the mean node and mean apogee come from Moshier's adjustment of ELP2000-85 with DE431
  corrections; the mean apogee lies on the inclined mean orbit, so it has a latitude;
- the true node and osculating apogee are osculating elements of the Moon's geocentric
  position and velocity, with GM(Earth + Moon); at the 27.55-day segment boundaries of the
  compressed Moon file they step by up to ~0.085 and ~0.9 arcsec (measured);
- their `distance_au` is Swiss Ephemeris's orbital distance and has no research meaning.

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
    "positions-v2: geocentric, apparent (light-time, aberration, solar light deflection); "
    "tropical, true ecliptic and true equinox of date; Jupiter-Pluto as system barycenters; "
    "mean node/apogee from Moshier-ELP2000-85, true node/osculating apogee from the Moon's "
    "osculating orbit with GM(Earth+Moon)"
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


class LunarPoint(enum.Enum):
    MEAN_NODE = "mean_node"
    TRUE_NODE = "true_node"
    MEAN_APOGEE = "mean_apogee"
    OSCULATING_APOGEE = "osculating_apogee"


type Point = Body | LunarPoint
ALL_POINTS: tuple[Point, ...] = (*Body, *LunarPoint)


@dataclass(frozen=True, kw_only=True)
class BodyPosition:
    body: Point  # a body or a lunar point
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

    def __getitem__(self, body: Point) -> BodyPosition:
        for position in self.bodies:
            if position.body is body:
                return position
        raise KeyError(body)
