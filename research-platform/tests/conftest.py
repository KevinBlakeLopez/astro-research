"""Shared fixtures for the platform's tests.

They need the PostgreSQL of research-platform/compose.yaml and the pinned ephemeris files, and
fail rather than skip when either is missing. The records are synthetic; no real person's birth
data is used as a fixture.
"""

from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest
from django.conf import settings

from astro_research.chart import BirthRecord
from astro_research.ephemeris import Ephemeris
from research_platform.models import Record, Source, Subject

START_DATABASE = "docker compose -f research-platform/compose.yaml up -d"


@pytest.fixture(scope="session", autouse=True)
def database_is_running() -> None:
    db = settings.DATABASES["default"]
    try:
        psycopg.connect(
            host=db["HOST"],
            port=db["PORT"],
            dbname=db["NAME"],
            user=db["USER"],
            password=db["PASSWORD"],
            connect_timeout=3,
        ).close()
    except psycopg.OperationalError as error:
        pytest.fail(f"PostgreSQL is not reachable ({error}); start it: {START_DATABASE}")


@pytest.fixture(scope="session")
def eph() -> Ephemeris:
    ephe = Path(settings.EPHE_DIR)
    if not ephe.is_dir():
        pytest.fail(f"{ephe} is missing: run `uv run python scripts/fetch_ephemeris.py`")
    return Ephemeris.open(ephe)


@pytest.fixture
def source() -> Source:
    return Source.objects.create(citation="A synthetic test source", reliability="AA")


@pytest.fixture
def subject() -> Subject:
    return Subject.objects.create(label="subject-1")


@pytest.fixture
def store(subject: Subject, source: Source) -> Callable[..., Record]:
    """Saves an engine `BirthRecord`, by default for `subject` from `source`, after validation."""

    def saved(
        birth_record: BirthRecord, subject: Subject = subject, source: Source = source
    ) -> Record:
        record = Record.from_birth_record(birth_record, subject=subject, source=source)
        record.full_clean()
        record.save()
        return record

    return saved
