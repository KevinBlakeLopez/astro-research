from pathlib import Path

import pytest

REPO_EPHE = Path(__file__).resolve().parents[1] / "ephe"


@pytest.fixture(scope="session")
def fetched_ephe() -> Path:
    """The pinned ephemeris files. Missing files fail the test; they never skip it."""
    if not REPO_EPHE.is_dir():
        pytest.fail("ephe/ is missing: run `uv run python scripts/fetch_ephemeris.py`")
    return REPO_EPHE
