"""Independent validation, layer 1: ephemeris data and light-time against JPL Horizons.

Reference values: tests/fixtures/horizons_astrometric.json, fetched once by
scripts/fetch_horizons_fixtures.py. They are geocentric astrometric RA/Dec in the ICRF, corrected
for light-time only. No precession-nutation model enters that frame, so the comparison isolates:

- the compressed DE441 data files (a faulty build, like 2026/04/14's Mercury, fails here);
- Swiss Ephemeris's light-time solution;
- the body definitions (Jupiter-Pluto are system barycenters) and the flags used.

It does not cover aberration, light deflection or the of-date frame; those are layers 2 and 3.
Horizons computes from JPL DE441, the same integration the pinned files are compressed from, so
this is independent of Swiss Ephemeris and its data builds, not of JPL's dynamics.

Tolerance, set before measuring, 2 mas on the sky:
- Swiss Ephemeris documents its compressed files as reproducing JPL to 0.001 arcsec;
- Horizons prints 1e-9 degree (0.004 mas);
- about 1 mas remains for differences between the two light-time iterations.

Correction after the first run (2026-11-03). Mars on 1850-01-01 missed by 2.415 mas. A geometric
heliocentric vector check against Horizons found the data itself off by 1.29 km at Mars and
0.22 km at Earth. Their difference (1.093 km) accounts for the whole geocentric miss, so
light-time is not involved. Swiss Ephemeris stores planets as offsets from heliocentric orbits,
so its compression error is a fixed size in space: seen from the Sun it was 1.12 mas (the
documented 1 mas is approximate), seen from Earth at 0.62 AU it was 2.4 mas. A planet's budget
is therefore 2 mas scaled by heliocentric / geocentric distance when that ratio exceeds 1. The
Sun and Moon stay at 2 mas.

A case above its budget is a finding to investigate, never a reason to widen the tolerance.

Until the positions slice adds the engine's own position API, these tests call `swisseph`
directly, after the environment has passed `verify_ephemeris()`.
"""

import json
import math
from pathlib import Path
from typing import Any

import pytest
import swisseph

from astro_research.ephemeris import EphemerisProvenance, load_manifest, verify_ephemeris

FIXTURE = Path(__file__).parent / "fixtures" / "horizons_astrometric.json"
TOLERANCE_MAS = 2.0
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


@pytest.fixture(scope="module", autouse=True)
def verified(fetched_ephe: Path) -> EphemerisProvenance:
    # Also points Swiss Ephemeris at the verified files, which is process-wide state.
    return verify_ephemeris(fetched_ephe)


def separation_mas(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Angle between two directions in milliarcseconds; atan2 stays accurate at tiny angles."""

    def unit(ra: float, dec: float) -> tuple[float, float, float]:
        r, d = math.radians(ra), math.radians(dec)
        return (math.cos(d) * math.cos(r), math.cos(d) * math.sin(r), math.sin(d))

    (ax, ay, az), (bx, by, bz) = unit(ra1, dec1), unit(ra2, dec2)
    cross = math.hypot(ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)
    return math.degrees(math.atan2(cross, ax * bx + ay * by + az * bz)) * 3.6e6


def budget_mas(name: str, jd_tt: float) -> float:
    """The tolerance for one case; see the module docstring for the derivation."""
    if name in ("Sun", "Moon"):
        return TOLERANCE_MAS
    # Distances only scale the allowance, so Swiss Ephemeris's own values are precise enough.
    geocentric = float(swisseph.calc(jd_tt, SE_BODIES[name], ASTROMETRIC)[0][2])
    heliocentric = float(
        swisseph.calc(jd_tt, SE_BODIES[name], ASTROMETRIC | swisseph.FLG_HELCTR)[0][2]
    )
    return TOLERANCE_MAS * max(1.0, heliocentric / geocentric)


def test_fixture_comes_from_the_pinned_de() -> None:
    sources = {body["source"] for body in load_fixture()["bodies"].values()}
    assert sources == {f"DE{load_manifest().de_number}"}


@pytest.mark.parametrize(("name", "row"), CASES)
def test_astrometric_position_matches_horizons(name: str, row: dict[str, Any]) -> None:
    position, retflag, serr = swisseph.calc(row["jd_tt"], SE_BODIES[name], ASTROMETRIC)
    assert retflag & swisseph.FLG_SWIEPH and not serr
    separation = separation_mas(
        position[0], position[1], float(row["ra_deg"]), float(row["dec_deg"])
    )
    budget = budget_mas(name, row["jd_tt"])
    assert separation <= budget, f"{separation:.3f} mas exceeds {budget:.3f} mas"
