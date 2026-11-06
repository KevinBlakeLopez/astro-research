"""Independent references for the lunar points, used by the layer 4 tests.

Mean node and mean apogee come from ERFA's IERS 2003 fundamental arguments (Simon et al. 1994).
The node is `faom03`. The mean perigee is F + Omega - l (`faf03 + faom03 - fal03`), and the mean
apogee is the point 180 degrees from it on the mean lunar orbit, projected onto the ecliptic with
the orbit's mean inclination. Both are referred to the mean equinox of date; nutation in longitude
(`nut06a`) moves them to the true equinox, where the engine reports them. Ecliptic latitude is
unaffected by nutation.

The true node and osculating apogee are osculating elements of Horizons' geometric geocentric Moon
state (tests/fixtures/horizons_moon_vectors.json), rotated onto the true ecliptic of date with the
same ERFA frame as reference_pipeline.py. Nothing here uses Swiss Ephemeris.
"""

import math

import erfa
import numpy as np
from reference_pipeline import to_true_ecliptic

GM_EARTH_KM3_S2 = 398600.435507  # JPL DE440/DE441
GM_MOON_KM3_S2 = 4902.800118  # JPL DE440/DE441
MEAN_INCLINATION_DEG = 5.145396  # mean inclination of the lunar orbit to the ecliptic


def _arguments(jd_tt: float) -> tuple[float, float, float, float]:
    """(Omega, F, l, nutation in longitude), radians, at `jd_tt` (TT)."""
    t = (jd_tt - 2451545.0) / 36525.0
    dpsi, _ = erfa.nut06a(2400000.5, jd_tt - 2400000.5)
    return erfa.faom03(t), erfa.faf03(t), erfa.fal03(t), dpsi


def mean_node(jd_tt: float) -> float:
    """Mean ascending node, ecliptic longitude on the true equinox of date, degrees."""
    node, _, _, dpsi = _arguments(jd_tt)
    return math.degrees(node + dpsi) % 360


def mean_apogee(jd_tt: float) -> tuple[float, float]:
    """Mean apogee on the inclined mean orbit: (longitude on the true equinox, latitude), deg."""
    node, f, anomaly, dpsi = _arguments(jd_tt)
    argument_of_latitude = (f + node - anomaly) + math.pi - node  # apogee, measured from the node
    incl = math.radians(MEAN_INCLINATION_DEG)
    along = math.atan2(
        math.sin(argument_of_latitude) * math.cos(incl), math.cos(argument_of_latitude)
    )
    latitude = math.asin(math.sin(argument_of_latitude) * math.sin(incl))
    return math.degrees(node + along + dpsi) % 360, math.degrees(latitude)


def osculating(
    jd_tt: float, position_km: np.ndarray, velocity_km_s: np.ndarray, mu: float
) -> tuple[float, float, float]:
    """Osculating (node longitude, apogee longitude, apogee latitude), degrees, true of date.

    The ICRF state is rotated onto the true ecliptic of date; the frame's own slow rotation
    (precession and nutation) is not added to the velocity.
    """
    matrix = to_true_ecliptic(jd_tt)
    r, v = matrix @ position_km, matrix @ velocity_km_s
    h = np.cross(r, v)
    node = math.degrees(math.atan2(h[0], -h[1])) % 360  # ascending node: z-axis x h
    apogee = -(np.cross(v, h) / mu - r / np.linalg.norm(r))  # opposite the eccentricity vector
    apogee /= np.linalg.norm(apogee)
    return (
        node,
        math.degrees(math.atan2(apogee[1], apogee[0])) % 360,
        math.degrees(math.asin(apogee[2])),
    )
