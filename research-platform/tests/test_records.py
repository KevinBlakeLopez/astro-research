"""Subjects, sources, birth records and datasets.

A birth record holds the engine's `BirthRecord` fields, and turns back into an equal
`BirthRecord`. A subject can have several records from different sources, which may disagree;
a dataset chooses records, not subjects, so which record stands for a subject is explicit.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from astro_research.chart import BirthRecord, TimeResolution
from astro_research.civil_time import LocalMeanTime, OffsetLocalTime, UtcTime, ZonedLocalTime
from astro_research.geo import GeoPosition
from research_platform.models import Dataset, Record, Source, Subject

LONDON = GeoPosition(latitude=51.5074, longitude=-0.1278)
BIRTH_RECORDS = {
    "utc": BirthRecord(
        time=UtcTime(utc=datetime(2024, 3, 20, 12, 0, 0, 123456, tzinfo=UTC)),
        place=GeoPosition(latitude=-33.8688, longitude=151.2093, elevation_m=58.0),
        stated_uncertainty=timedelta(seconds=90),
    ),
    "zoned": BirthRecord(
        time=ZonedLocalTime(local=datetime(1987, 6, 15, 4, 25), zone="Europe/London"),
        place=LONDON,
        time_resolution=TimeResolution.MINUTE,
    ),
    "zoned, pre-1970 with a stated offset": BirthRecord(
        time=ZonedLocalTime(
            local=datetime(1955, 10, 30, 1, 30),  # happened twice; EDT is stated
            zone="America/New_York",
            stated_utc_offset=timedelta(hours=-4),
            accept_pre_1970_tzdb=True,
        ),
        place=GeoPosition(latitude=40.7128, longitude=-74.0060),
    ),
    "offset": BirthRecord(
        time=OffsetLocalTime(
            local=datetime(1972, 6, 30, 23, 0), utc_offset=timedelta(hours=5, minutes=30)
        ),
        place=GeoPosition(latitude=19.0760, longitude=72.8777),
        time_resolution=TimeResolution.HOUR,
    ),
    "local mean time": BirthRecord(
        time=LocalMeanTime(local=datetime(1850, 3, 1, 6, 0)),
        place=GeoPosition(latitude=69.6492, longitude=18.9553),
        time_resolution=TimeResolution.HOUR,
    ),
}


@pytest.mark.django_db
@pytest.mark.parametrize("kind", list(BIRTH_RECORDS))
def test_a_record_round_trips_through_the_database(store: Callable[..., Record], kind: str) -> None:
    record = store(BIRTH_RECORDS[kind])
    assert Record.objects.get(pk=record.pk).birth_record() == BIRTH_RECORDS[kind]


@pytest.mark.django_db
def test_a_record_the_engine_refuses_is_not_valid(subject: Subject, source: Source) -> None:
    # The engine checks every field when it builds the record; the model asks it to.
    record = Record.from_birth_record(BIRTH_RECORDS["zoned"], subject=subject, source=source)
    record.latitude = 95.0
    with pytest.raises(ValidationError, match="latitude"):
        record.full_clean()
    finer = Record.from_birth_record(BIRTH_RECORDS["offset"], subject=subject, source=source)
    finer.reading = "1972-06-30T23:31:00"  # finer than its stated resolution, an hour
    with pytest.raises(ValidationError, match="resolution"):
        finer.full_clean()


MISMATCHED_FIELDS: dict[str, tuple[str, dict[str, object]]] = {
    "a UTC reading without its offset": ("utc", {"reading": "2024-03-20T12:00:00"}),
    "a zone on a UTC record": ("utc", {"zone": "Europe/London"}),
    "an offset on a UTC record": ("utc", {"utc_offset_us": 0}),
    "a local reading with an offset": ("zoned", {"reading": "1987-06-15T04:25:00+01:00"}),
    "an offset time without its offset": ("offset", {"utc_offset_us": None}),
    "an offset on local mean time": ("local mean time", {"utc_offset_us": 3_600_000_000}),
    "accepting pre-1970 tzdb without a zone": ("offset", {"accept_pre_1970_tzdb": True}),
    "a reading that isn't ISO 8601": ("zoned", {"reading": "15 June 1987, 4:25"}),
}


@pytest.mark.django_db
@pytest.mark.parametrize("mismatch", list(MISMATCHED_FIELDS))
def test_fields_that_describe_no_one_kind_of_civil_time_are_not_valid(
    subject: Subject, source: Source, mismatch: str
) -> None:
    kind, changes = MISMATCHED_FIELDS[mismatch]
    record = Record.from_birth_record(BIRTH_RECORDS[kind], subject=subject, source=source)
    for name, value in changes.items():
        setattr(record, name, value)
    with pytest.raises(ValidationError):
        record.full_clean()


@pytest.mark.django_db
def test_a_subject_can_have_records_from_several_sources(
    store: Callable[..., Record], subject: Subject
) -> None:
    other = Source.objects.create(citation="Another synthetic source", reliability="C")
    first = store(BIRTH_RECORDS["zoned"])
    later = BirthRecord(
        time=ZonedLocalTime(local=datetime(1987, 6, 15, 4, 40), zone="Europe/London"),
        place=LONDON,
    )
    second = store(later, source=other)
    assert set(subject.records.all()) == {first, second}
    assert first.source != second.source


@pytest.mark.django_db
def test_a_dataset_chooses_records_not_subjects(store: Callable[..., Record]) -> None:
    chosen = store(BIRTH_RECORDS["zoned"])
    store(BIRTH_RECORDS["utc"])  # the same subject's other record
    dataset = Dataset.objects.create(name="sample", description="A synthetic sample")
    dataset.records.add(chosen)
    assert list(dataset.records.all()) == [chosen]


@pytest.mark.django_db
def test_labels_and_names_are_unique() -> None:
    Subject.objects.create(label="twin")
    Dataset.objects.create(name="sample")
    with transaction.atomic(), pytest.raises(IntegrityError):
        Subject.objects.create(label="twin")
    with transaction.atomic(), pytest.raises(IntegrityError):
        Dataset.objects.create(name="sample")
