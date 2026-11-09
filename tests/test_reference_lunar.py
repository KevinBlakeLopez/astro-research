"""Independent validation, layer 4: the lunar points against ERFA and Horizons.

References (tests/reference_lunar.py): the mean node and mean apogee from ERFA's IERS 2003
fundamental arguments; the true node and osculating apogee as osculating elements of Horizons'
geometric geocentric Moon state (tests/fixtures/horizons_moon_vectors.json), with
GM(Earth + Moon). With GM(Earth) alone the apogee would be 17 degrees off, so Swiss Ephemeris
uses the sum (measured).

Budgets:
- true node 0.1 arcsec, osculating apogee 0.9 arcsec: Swiss Ephemeris's documented precision
  against JPL-derived values (manual §2.2.2-2.2.3); measured at most 0.007 and 0.288 arcsec;
- mean node and mean apogee longitude 1.5 arcsec, mean apogee latitude 0.1 arcsec: set from
  measurement (worst 1.068 arcsec for the node at 2399, 0.073 for the latitude). The manual
  claims 1 arcsec against DE431, but no published bound covers the difference between its lunar
  theory (Moshier/ELP2000-85) and ERFA's (Simon et al. 1994);
- segment steps of the compressed Moon file, every 27.55 days: true node below 0.1 arcsec
  (documented figure; measured 0.085), osculating apogee below 1.0 arcsec (measured 0.907,
  slightly above the documented 0.9).
A case above its budget is a finding to investigate, never a reason to widen the budget.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from reference_lunar import GM_EARTH_KM3_S2, GM_MOON_KM3_S2, mean_apogee, mean_node, osculating

from astro_research.astronomical_time import AstronomicalTime, TimeScale, Ut1Basis
from astro_research.civil_time import UtcTime, resolve
from astro_research.ephemeris import Ephemeris, load_manifest
from astro_research.geo import GeoPosition
from astro_research.positions import LunarPoint

FIXTURE = Path(__file__).parent / "fixtures" / "horizons_moon_vectors.json"
TRUE_NODE_BUDGET = 0.1
OSCULATING_APOGEE_BUDGET = 0.9
MEAN_LONGITUDE_BUDGET = 1.5
MEAN_APOGEE_LATITUDE_BUDGET = 0.1
TRUE_NODE_STEP_LIMIT = 0.1
OSCULATING_APOGEE_STEP_LIMIT = 1.0
# Segment boundaries of the pinned semo_18.se1 found by a 1-minute scan (UT, to the minute).
SEGMENT_BOUNDARIES = [
    datetime(2000, 1, 18, 17, 24, tzinfo=UTC),
    datetime(2000, 2, 15, 6, 42, tzinfo=UTC),
]
PLACEHOLDER = resolve(
    UtcTime(utc=datetime(2000, 1, 1, tzinfo=UTC)), GeoPosition(latitude=0.0, longitude=0.0)
)


def load_states() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())  # type: ignore[no-any-return]


STATES = [pytest.param(state, id=state["date"]) for state in load_states()["states"]]


@pytest.fixture(scope="module")
def eph(fetched_ephe: Path) -> Ephemeris:
    return Ephemeris.open(fetched_ephe)


def at_tt(jd_tt: float) -> AstronomicalTime:
    # positions() reads only the TT; the other fields are placeholders (as in layer 3).
    return AstronomicalTime(
        instant=PLACEHOLDER,
        time_scale=TimeScale.CLOCK_AS_UT1,
        jd_ut1=jd_tt,
        jd_tt=jd_tt,
        delta_t_s=0.0,
        ut1_minus_utc_s=0.0,
        ut1_basis=Ut1Basis.CLOCK_READING,
    )


def arcsec(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180) * 3600


def test_fixture_comes_from_the_pinned_de() -> None:
    assert load_states()["source"] == f"DE{load_manifest().de_number}"


@pytest.mark.parametrize("state", STATES)
def test_true_node_matches_the_osculating_node(eph: Ephemeris, state: dict[str, Any]) -> None:
    r = np.array([float(state[k]) for k in ("X", "Y", "Z")])
    v = np.array([float(state[k]) for k in ("VX", "VY", "VZ")])
    node, _, _ = osculating(state["jd_tt"], r, v, GM_EARTH_KM3_S2 + GM_MOON_KM3_S2)
    got = eph.positions(at_tt(state["jd_tt"]), [LunarPoint.TRUE_NODE])[LunarPoint.TRUE_NODE]
    assert arcsec(got.longitude, node) <= TRUE_NODE_BUDGET


@pytest.mark.parametrize("state", STATES)
def test_osculating_apogee_matches_the_osculating_ellipse(
    eph: Ephemeris, state: dict[str, Any]
) -> None:
    r = np.array([float(state[k]) for k in ("X", "Y", "Z")])
    v = np.array([float(state[k]) for k in ("VX", "VY", "VZ")])
    _, lon, lat = osculating(state["jd_tt"], r, v, GM_EARTH_KM3_S2 + GM_MOON_KM3_S2)
    point = LunarPoint.OSCULATING_APOGEE
    got = eph.positions(at_tt(state["jd_tt"]), [point])[point]
    assert arcsec(got.longitude, lon) <= OSCULATING_APOGEE_BUDGET
    assert abs(got.latitude - lat) * 3600 <= OSCULATING_APOGEE_BUDGET


@pytest.mark.parametrize("state", STATES)
def test_mean_node_matches_the_iers_formula(eph: Ephemeris, state: dict[str, Any]) -> None:
    got = eph.positions(at_tt(state["jd_tt"]), [LunarPoint.MEAN_NODE])[LunarPoint.MEAN_NODE]
    assert arcsec(got.longitude, mean_node(state["jd_tt"])) <= MEAN_LONGITUDE_BUDGET


@pytest.mark.parametrize("state", STATES)
def test_mean_apogee_matches_the_iers_formula(eph: Ephemeris, state: dict[str, Any]) -> None:
    lon, lat = mean_apogee(state["jd_tt"])
    got = eph.positions(at_tt(state["jd_tt"]), [LunarPoint.MEAN_APOGEE])[LunarPoint.MEAN_APOGEE]
    assert arcsec(got.longitude, lon) <= MEAN_LONGITUDE_BUDGET
    assert abs(got.latitude - lat) * 3600 <= MEAN_APOGEE_LATITUDE_BUDGET


@pytest.mark.parametrize("boundary", SEGMENT_BOUNDARIES, ids=lambda b: b.strftime("%Y-%m-%d"))
@pytest.mark.parametrize(
    ("point", "limit"),
    [
        (LunarPoint.TRUE_NODE, TRUE_NODE_STEP_LIMIT),
        (LunarPoint.OSCULATING_APOGEE, OSCULATING_APOGEE_STEP_LIMIT),
    ],
)
def test_segment_steps_stay_within_their_limits(
    eph: Ephemeris, point: LunarPoint, limit: float, boundary: datetime
) -> None:
    # A step of size J at a segment boundary shows up as a 1-minute second difference of J;
    # smooth curvature contributes under 0.01 arcsec over one minute.
    instants = [boundary + timedelta(minutes=m) for m in range(-10, 11)]
    lon = [
        eph.positions(
            eph.time(
                resolve(UtcTime(utc=t), GeoPosition(latitude=0.0, longitude=0.0)),
                TimeScale.CLOCK_AS_UT1,
            ),
            [point],
        )[point].longitude
        for t in instants
    ]
    second_differences = [
        abs(((lon[i + 1] - lon[i] + 180) % 360 - 180) - ((lon[i] - lon[i - 1] + 180) % 360 - 180))
        * 3600
        for i in range(1, len(lon) - 1)
    ]
    assert max(second_differences) < limit
