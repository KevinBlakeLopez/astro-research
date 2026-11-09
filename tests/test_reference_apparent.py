"""Independent validation, layer 3: apparent positions end to end, against Horizons + ERFA.

The engine's output (`Ephemeris.positions()`: ecliptic longitude and latitude, RA and Dec,
apparent and of date) is compared with tests/reference_pipeline.py. That pipeline starts from
Horizons' astrometric place and applies ERFA's implementation of the models Swiss Ephemeris uses,
so a residual measures implementation rather than a choice of model.

Budget per case, checked separately for the ecliptic and the equatorial direction:
- ephemeris data and light-time: layer 1's budget (2 mas, scaled for planets near Earth);
- nutation series: 1.0 mas. Swiss Ephemeris describes its default series as having a
  "precision of milli-arcsec"; the IAU documents 2000B as within 1 mas of 2000A over
  1995-2050; measured at most 1.00 mas against 2000A at the fixture dates (1800-2400);
- obliquity series: 0.5 mas, measured only (at most 0.51 mas; no documented bound).
That is 3.5 mas for the Sun and Moon. A case above its budget is a finding to investigate,
never a reason to widen the budget.

Positions are requested at the fixture's TT directly: `Ephemeris.positions()` reads only the
time's TT, and the other `AstronomicalTime` fields here are placeholders.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from horizons_fixture import CASES, data_budget_mas, separation_mas
from reference_pipeline import apparent_position

from astro_research.astronomical_time import AstronomicalTime, TimeScale, Ut1Basis
from astro_research.civil_time import UtcTime, resolve
from astro_research.ephemeris import Ephemeris
from astro_research.geo import GeoPosition
from astro_research.positions import Body

NUTATION_ALLOWANCE_MAS = 1.0
OBLIQUITY_ALLOWANCE_MAS = 0.5
PLACEHOLDER_INSTANT = resolve(
    UtcTime(utc=datetime(2000, 1, 1, tzinfo=UTC)), GeoPosition(latitude=0.0, longitude=0.0)
)


@pytest.fixture(scope="module")
def eph(fetched_ephe: Path) -> Ephemeris:
    return Ephemeris.open(fetched_ephe)


def at_tt(jd_tt: float) -> AstronomicalTime:
    return AstronomicalTime(
        instant=PLACEHOLDER_INSTANT,
        time_scale=TimeScale.CLOCK_AS_UT1,
        jd_ut1=jd_tt,
        jd_tt=jd_tt,
        delta_t_s=0.0,
        ut1_minus_utc_s=0.0,
        ut1_basis=Ut1Basis.CLOCK_READING,
    )


@pytest.mark.parametrize(("name", "row"), CASES)
def test_apparent_position_matches_the_reference(
    eph: Ephemeris, name: str, row: dict[str, Any]
) -> None:
    body = Body[name.upper()]
    got = eph.positions(at_tt(row["jd_tt"]), [body])[body]
    ref = apparent_position(
        row["jd_tt"],
        float(row["ra_deg"]),
        float(row["dec_deg"]),
        float(row["delta_au"]),
        deflect=body is not Body.SUN,
    )
    budget = data_budget_mas(name, row["jd_tt"]) + NUTATION_ALLOWANCE_MAS + OBLIQUITY_ALLOWANCE_MAS
    ecliptic = separation_mas(got.longitude, got.latitude, ref.longitude, ref.latitude)
    equatorial = separation_mas(
        got.right_ascension, got.declination, ref.right_ascension, ref.declination
    )
    assert ecliptic <= budget, f"ecliptic {ecliptic:.3f} mas exceeds {budget:.3f} mas"
    assert equatorial <= budget, f"equatorial {equatorial:.3f} mas exceeds {budget:.3f} mas"
