"""An independent reference for apparent geocentric positions, used by the layer 3 tests.

Geometry comes from JPL Horizons (astrometric ICRF direction and light-time-corrected distance,
tests/fixtures/horizons_astrometric.json). Every reduction step comes from ERFA, the open
implementation of the IAU's SOFA reference library. Nothing here uses Swiss Ephemeris.

The models are the ones Swiss Ephemeris documents and was measured to use, each implemented
independently, so a residual measures implementation rather than a choice of model:

1. light deflection by the Sun (`ld`, for a source at finite distance);
2. aberration with Earth's barycentric velocity (`ab`, velocity from ERFA's `epv00`);
3. frame bias and precession, Vondrak 2011 (`ltpb`): Swiss Ephemeris's default for both its
   short- and long-term models (swephexp.h:522, 527 at commit 5ae0bce);
4. nutation, IAU 2000A (`nut06a`): Swiss Ephemeris's values track it to within 1 mas over
   1800-2400, though its default is named 2000B (it differs from ERFA's 2000B by up to 6 mas);
5. the true ecliptic of date, using the IAU 2006 mean obliquity (`obl06`) plus nutation:
   Swiss Ephemeris's obliquity is within 0.51 mas of it, while the angle between Vondrak's own
   equator and ecliptic poles differs by up to 5.3 mas.

The resulting frame is not the pure IAU 2006 one (`pnm06a`); against that the engine differs by
up to 3 mas over 1800-2100 and 5.4 mas at 2399, a documented convention.
"""

import math
import warnings
from dataclasses import dataclass

import erfa
import numpy as np

_MJD_ZERO = 2400000.5
_C_AU_PER_DAY = erfa.CMPS * erfa.DAYSEC / erfa.DAU


@dataclass(frozen=True, kw_only=True)
class ReferencePosition:
    longitude: float  # degrees, true ecliptic and equinox of date
    latitude: float
    right_ascension: float  # degrees, true equator and equinox of date
    declination: float


def apparent_position(
    jd_tt: float, ra_deg: float, dec_deg: float, delta_au: float, *, deflect: bool
) -> ReferencePosition:
    """Apparent place of a solar-system body from its astrometric place, at `jd_tt` (TT)."""
    d1, d2 = _MJD_ZERO, jd_tt - _MJD_ZERO
    with warnings.catch_warnings():
        # epv00 warns outside 1900-2100. Its velocity direction then stays within 24 mas of
        # Swiss Ephemeris's (measured), which moves a ~20 arcsec aberration by < 0.01 mas.
        warnings.simplefilter("ignore", erfa.ErfaWarning)
        heliocentric, barycentric = erfa.epv00(d1, d2)  # Earth; TDB, taken as TT (< 2 ms apart)
    earth_from_sun = heliocentric["p"]
    sun_distance = float(np.linalg.norm(earth_from_sun))

    direction = unit_vector(ra_deg, dec_deg)
    if deflect:
        source_from_sun = delta_au * direction + earth_from_sun
        direction = erfa.ld(
            1.0,
            direction,
            source_from_sun / np.linalg.norm(source_from_sun),
            earth_from_sun / sun_distance,
            sun_distance,
            1e-10,
        )

    velocity = barycentric["v"] / _C_AU_PER_DAY
    apparent = erfa.ab(direction, velocity, sun_distance, math.sqrt(1 - velocity @ velocity))

    dpsi, deps = erfa.nut06a(d1, d2)
    mean_obliquity = erfa.obl06(d1, d2)
    to_true_of_date = erfa.numat(mean_obliquity, dpsi, deps) @ erfa.ltpb(erfa.epj(d1, d2))
    x, y, z = to_true_of_date @ apparent

    obliquity = mean_obliquity + deps
    ecliptic_y = y * math.cos(obliquity) + z * math.sin(obliquity)
    ecliptic_z = -y * math.sin(obliquity) + z * math.cos(obliquity)
    return ReferencePosition(
        longitude=math.degrees(math.atan2(ecliptic_y, x)) % 360,
        latitude=math.degrees(math.asin(ecliptic_z)),
        right_ascension=math.degrees(math.atan2(y, x)) % 360,
        declination=math.degrees(math.asin(z)),
    )


def unit_vector(ra_deg: float, dec_deg: float) -> np.ndarray:
    ra, dec = math.radians(ra_deg), math.radians(dec_deg)
    return np.array([math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec)])
