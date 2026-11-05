"""Shared access to the Horizons reference fixture, for the layer 1 and layer 3 tests."""

import json
import math
from pathlib import Path
from typing import Any

import pytest
import swisseph

FIXTURE = Path(__file__).parent / "fixtures" / "horizons_astrometric.json"
DATA_TOLERANCE_MAS = 2.0  # derivation: tests/test_reference_horizons.py docstring
ASTROMETRIC = (
    swisseph.FLG_SWIEPH
    | swisseph.FLG_EQUATORIAL
    | swisseph.FLG_J2000
    | swisseph.FLG_ICRS
    | swisseph.FLG_NONUT
    | swisseph.FLG_NOABERR
    | swisseph.FLG_NOGDEFL
)
SE_BODIES = {
    "Sun": swisseph.SUN,
    "Moon": swisseph.MOON,
    "Mercury": swisseph.MERCURY,
    "Venus": swisseph.VENUS,
    "Mars": swisseph.MARS,
    "Jupiter": swisseph.JUPITER,
    "Saturn": swisseph.SATURN,
    "Uranus": swisseph.URANUS,
    "Neptune": swisseph.NEPTUNE,
    "Pluto": swisseph.PLUTO,
}


def load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())  # type: ignore[no-any-return]


CASES = [
    pytest.param(name, row, id=f"{name}-{row['date']}")
    for name, body in load_fixture()["bodies"].items()
    for row in body["rows"]
]


def separation_mas(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Angle between two directions in milliarcseconds; atan2 stays accurate at tiny angles."""

    def unit(ra: float, dec: float) -> tuple[float, float, float]:
        r, d = math.radians(ra), math.radians(dec)
        return (math.cos(d) * math.cos(r), math.cos(d) * math.sin(r), math.sin(d))

    (ax, ay, az), (bx, by, bz) = unit(ra1, dec1), unit(ra2, dec2)
    cross = math.hypot(ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)
    return math.degrees(math.atan2(cross, ax * bx + ay * by + az * bz)) * 3.6e6


def data_budget_mas(name: str, jd_tt: float) -> float:
    """Allowance for ephemeris data and light-time in one case (layer 1's budget).

    2 mas, scaled for planets by heliocentric / geocentric distance when they are nearer Earth
    than the Sun: the compression error is a fixed size in heliocentric space.
    """
    if name in ("Sun", "Moon"):
        return DATA_TOLERANCE_MAS
    # Distances only scale the allowance, so Swiss Ephemeris's own values are precise enough.
    geocentric = float(swisseph.calc(jd_tt, SE_BODIES[name], ASTROMETRIC)[0][2])
    heliocentric = float(
        swisseph.calc(jd_tt, SE_BODIES[name], ASTROMETRIC | swisseph.FLG_HELCTR)[0][2]
    )
    return DATA_TOLERANCE_MAS * max(1.0, heliocentric / geocentric)
