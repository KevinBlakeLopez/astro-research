from collections.abc import Iterator
from pathlib import Path

import pytest
import swisseph

REPO_EPHE = Path(__file__).resolve().parents[1] / "ephe"


@pytest.fixture(scope="session")
def fetched_ephe() -> Path:
    """The pinned ephemeris files. Missing files fail the test; they never skip it."""
    if not REPO_EPHE.is_dir():
        pytest.fail("ephe/ is missing: run `uv run python scripts/fetch_ephemeris.py`")
    return REPO_EPHE


@pytest.fixture(autouse=True)
def delta_t_left_as_swiss_ephemeris_s_model() -> Iterator[None]:
    """Swiss Ephemeris keeps one delta-T setting for the whole process. A calculation that sets
    its own must put the model back, or every later one silently uses that constant."""
    yield
    constant = swisseph.deltat(2415020.5) == swisseph.deltat(2451545.0)  # 1900 and 2000
    swisseph.set_delta_t_userdef(swisseph.DELTAT_AUTOMATIC)
    assert not constant, "a calculation left Swiss Ephemeris's delta-T set to a constant"
