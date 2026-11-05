"""Independent validation, layer 2: Swiss Ephemeris's reduction models, as convention guards.

The engine inherits Swiss Ephemeris's models. Measured against ERFA (pyerfa, the IAU's SOFA
library) at the nine fixture dates (1800-2400), they are:
- precession: Vondrak 2011, as its source names (swephexp.h:522, 527 at commit 5ae0bce);
- mean obliquity: within 0.51 mas of IAU 2006, not the angle between Vondrak's own poles;
- nutation: within 1.0 mas of IAU 2000A, although its default is named 2000B.
Layer 3's reference and budget rest on these facts, so a Swiss Ephemeris release that changed a
default would fail here before any position moved silently. Tolerances are the measured maxima
rounded up to two decimals.
"""

import math
from pathlib import Path

import erfa
import pytest
import swisseph
from horizons_fixture import load_fixture, separation_mas
from reference_pipeline import unit_vector

from astro_research.ephemeris import Ephemeris

MAS_PER_DEGREE = 3.6e6
PRECESSION_TOLERANCE_MAS = 1.32  # measured 1.3105 mas (2100-01-01)
OBLIQUITY_TOLERANCE_MAS = 0.51  # measured 0.5057 mas (2399-12-15)
NUTATION_TOLERANCE_MAS = 1.0  # measured 0.9958 mas in dpsi, 0.8673 in deps (2100-01-01)
DATES = [
    pytest.param(row["jd_tt"], id=row["date"]) for row in load_fixture()["bodies"]["Sun"]["rows"]
]


@pytest.fixture(scope="module", autouse=True)
def verified(fetched_ephe: Path) -> Ephemeris:
    return Ephemeris.open(fetched_ephe)


def two_part(jd_tt: float) -> tuple[float, float]:
    return 2400000.5, jd_tt - 2400000.5


@pytest.mark.parametrize("jd_tt", DATES)
def test_precession_is_vondrak_2011(jd_tt: float) -> None:
    # Rotate Swiss Ephemeris's own apparent ICRS direction of the Sun with ERFA's Vondrak
    # precession and IAU 2000A nutation; compare with Swiss Ephemeris's own of-date direction.
    icrs_flags = (
        swisseph.FLG_SWIEPH
        | swisseph.FLG_EQUATORIAL
        | swisseph.FLG_J2000
        | swisseph.FLG_ICRS
        | swisseph.FLG_NONUT
    )
    icrs = swisseph.calc(jd_tt, swisseph.SUN, icrs_flags)[0]
    of_date = swisseph.calc(jd_tt, swisseph.SUN, swisseph.FLG_SWIEPH | swisseph.FLG_EQUATORIAL)[0]
    d1, d2 = two_part(jd_tt)
    dpsi, deps = erfa.nut06a(d1, d2)
    rotation = erfa.numat(erfa.obl06(d1, d2), dpsi, deps) @ erfa.ltpb(erfa.epj(d1, d2))
    x, y, z = rotation @ unit_vector(icrs[0], icrs[1])
    erfa_ra, erfa_dec = math.degrees(math.atan2(y, x)) % 360, math.degrees(math.asin(z))
    assert separation_mas(of_date[0], of_date[1], erfa_ra, erfa_dec) <= PRECESSION_TOLERANCE_MAS


@pytest.mark.parametrize("jd_tt", DATES)
def test_mean_obliquity_matches_iau_2006(jd_tt: float) -> None:
    mean_obliquity = swisseph.calc(jd_tt, swisseph.ECL_NUT, 0)[0][1]
    iau_2006 = math.degrees(erfa.obl06(*two_part(jd_tt)))
    assert abs(mean_obliquity - iau_2006) * MAS_PER_DEGREE <= OBLIQUITY_TOLERANCE_MAS


@pytest.mark.parametrize("jd_tt", DATES)
def test_nutation_tracks_iau_2000a(jd_tt: float) -> None:
    _, _, dpsi, deps, _, _ = swisseph.calc(jd_tt, swisseph.ECL_NUT, 0)[0]
    erfa_dpsi, erfa_deps = (math.degrees(v) for v in erfa.nut06a(*two_part(jd_tt)))
    assert abs(dpsi - erfa_dpsi) * MAS_PER_DEGREE <= NUTATION_TOLERANCE_MAS
    assert abs(deps - erfa_deps) * MAS_PER_DEGREE <= NUTATION_TOLERANCE_MAS
