"""Charts from AstroChart Bridge: its `.astrochart.json` files, version 1, imported as records.

`research_platform.astrochart.read` reads a file's text as AstroChart Bridge's own reader does,
refusing what it refuses with the same reason: it's checked against `conformance.json`, made
with AstroChart Bridge's `parse()` (`fixtures/astrochart/README.md`).

`python manage.py import_charts FILE... --source ID` imports a batch, all or nothing:

- each chart becomes an offset-time record: the wall-clock reading and the offset its source
  stated, which is the engine's `OffsetLocalTime`, so no time zone is involved. Time resolution
  is left blank unless `--resolution` states it, and elevation is the platform's default (0 m):
  the format has neither;
- the source is yours (`--source`), never the site AstroChart Bridge read the chart from;
- the subject is the file's name (`labels.name`), or `--subject` for every file of the batch.
  A file with neither is refused, and so is a name that's already a subject, or that another
  file of the batch also gives, unless `--add-to-existing` says they're the same person;
- each file is kept exactly as imported (`ImportedChart`), with its SHA-256, which is unique,
  and its site, via, zone label and UTC basis in columns of their own;
- if any file is refused, by the format, by the engine or as a duplicate, nothing is imported
  and every refused file is listed with its reason. `--check` checks without importing, and
  `--dataset` adds the records to a dataset.

Every chart here is synthetic.
"""

import hashlib
import io
import json
import math
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import Client

from astro_research.chart import BirthRecord, TimeResolution
from astro_research.civil_time import OffsetLocalTime
from astro_research.geo import GeoPosition
from research_platform.astrochart import RefusedChart, read
from research_platform.models import Dataset, ImportedChart, Record, Source, Subject

FIXTURES = Path(__file__).parent / "fixtures" / "astrochart"
CONFORMANCE = json.loads((FIXTURES / "conformance.json").read_text(encoding="utf-8"))
BST = FIXTURES / "chart-1987-06-15.astrochart.json"
HALF_HOUR = FIXTURES / "chart-1990-01-01.astrochart.json"
LMT = FIXTURES / "chart-1890-03-01.astrochart.json"  # no name
SOUTHERN = FIXTURES / "chart-2000-01-01.astrochart.json"
PASTED = FIXTURES / "chart-1975-07-04.astrochart.json"
NAMED = (BST, HALF_HOUR, SOUTHERN, PASTED)


def birth(local: datetime, offset_s: int, latitude: float, longitude: float) -> BirthRecord:
    return BirthRecord(
        time=OffsetLocalTime(local=local, utc_offset=timedelta(seconds=offset_s)),
        place=GeoPosition(latitude=latitude, longitude=longitude),
    )


EXPECTED = {
    BST: ("Synthetic Person One", birth(datetime(1987, 6, 15, 4, 25), 3600, 51.5074, -0.1278)),
    HALF_HOUR: ("Synthetic Person Two", birth(datetime(1990, 1, 1, 12), 19800, 28.6139, 77.209)),
    SOUTHERN: (
        "Synthetic Person Three",
        birth(datetime(2000, 1, 1, 0, 30), 39600, -33.8688, 151.2093),
    ),
    PASTED: (
        "Synthetic Person Four",
        birth(datetime(1975, 7, 4, 16, 45), -14400, 40.7128, -74.006),
    ),
}


def run(*paths: Path, **options: Any) -> str:
    out = io.StringIO()
    call_command("import_charts", *(str(path) for path in paths), stdout=out, **options)
    return out.getvalue()


def refusal(*paths: Path, **options: Any) -> str:
    with pytest.raises(CommandError) as raised:
        run(*paths, **options)
    message = str(raised.value)
    assert "nothing was imported" in message
    return message


def nothing_saved() -> bool:
    return not (
        Record.objects.exists() or ImportedChart.objects.exists() or Subject.objects.exists()
    )


def edited(path: Path, tmp_path: Path, name: str, change: Any) -> Path:
    """A copy of a fixture under `name`, with `change` applied to its document."""
    document = json.loads(path.read_text(encoding="utf-8"))
    change(document)
    copy = tmp_path / name
    copy.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return copy


# --- The reader -----------------------------------------------------------------------------


@pytest.mark.parametrize("case", CONFORMANCE, ids=[case["name"] for case in CONFORMANCE])
def test_the_reader_agrees_with_astrochart_bridge_s(case: dict[str, Any]) -> None:
    if "refused" in case:
        with pytest.raises(RefusedChart) as raised:
            read(case["text"])
        assert raised.value.reason == case["refused"]
        return
    chart = read(case["text"])
    expected = case["read"]
    assert chart.local == expected["chart"]["local"]
    assert chart.utc_offset_seconds == expected["chart"]["utcOffsetSeconds"]
    assert type(chart.utc_offset_seconds) is int
    for ours, theirs in (
        (chart.latitude, expected["chart"]["latitude"]),
        (chart.longitude, expected["chart"]["longitude"]),
    ):  # -0 is read as 0: a stored chart keeps -0.0, so it would change the calculation key
        assert ours == theirs and math.copysign(1.0, ours) == math.copysign(1.0, theirs)
    assert chart.source == expected["source"]  # unknown fields dropped
    assert chart.labels == expected.get("labels", {})


def test_the_fixtures_read_as_written() -> None:
    chart = read(SOUTHERN.read_text(encoding="utf-8"))
    assert (chart.local, chart.utc_offset_seconds) == ("2000-01-01T00:30:00", 39600)
    assert (chart.latitude, chart.longitude) == (-33.8688, 151.2093)
    assert chart.source == {
        "site": "astro-seek",
        "via": "screenshot",
        "zoneLabel": "AEDT",
        "utcBasis": "zone-label",
    }
    assert chart.labels == {"name": "Synthetic Person Three", "town": "Sydney, AU"}


# --- Importing ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_charts_become_offset_time_records(source: Source) -> None:
    out = run(*NAMED, source=source.pk)
    assert "imported 4 charts" in out
    for path, (label, expected) in EXPECTED.items():
        record = Record.objects.get(imported_chart__file_name=path.name)
        assert record.subject.label == label and record.source == source
        assert record.time_kind == Record.TimeKind.OFFSET_LOCAL_TIME and record.zone == ""
        assert record.time_resolution == "" and record.elevation_m == 0.0
        assert record.birth_record() == expected


@pytest.mark.django_db
def test_a_local_mean_time_offset_is_kept_to_the_second(source: Source) -> None:
    run(LMT, source=source.pk, subject="Synthetic Person Five")
    record = Record.objects.get()
    assert record.subject.label == "Synthetic Person Five"
    assert record.utc_offset_us == 561_000_000
    assert record.birth_record() == birth(datetime(1890, 3, 1, 10), 561, 48.8566, 2.3522)


@pytest.mark.django_db
def test_each_file_is_kept_exactly_with_its_provenance(source: Source) -> None:
    run(SOUTHERN, PASTED, source=source.pk)
    southern = ImportedChart.objects.get(file_name=SOUTHERN.name)
    assert southern.text == SOUTHERN.read_text(encoding="utf-8")
    assert southern.sha256 == hashlib.sha256(SOUTHERN.read_bytes()).hexdigest()
    assert (southern.site, southern.via, southern.zone_label, southern.utc_basis) == (
        "astro-seek",
        "screenshot",
        "AEDT",
        "zone-label",
    )
    assert southern.record.subject.label == "Synthetic Person Three"
    pasted = ImportedChart.objects.get(file_name=PASTED.name)
    assert (pasted.site, pasted.via, pasted.zone_label, pasted.utc_basis) == (
        "",
        "paste",
        "",
        "entered",
    )


@pytest.mark.django_db
def test_the_resolution_option_states_the_batch_s_resolution(source: Source) -> None:
    run(BST, HALF_HOUR, source=source.pk, resolution="minute")
    for record in Record.objects.all():
        assert record.time_resolution == "minute"
        assert record.birth_record().time_resolution == TimeResolution.MINUTE


@pytest.mark.django_db
def test_the_dataset_option_adds_the_records(source: Source) -> None:
    dataset = Dataset.objects.create(name="imported")
    run(*NAMED, source=source.pk, dataset="imported")
    assert set(dataset.records.all()) == set(Record.objects.all())
    assert dataset.records.count() == 4


@pytest.mark.django_db
def test_check_imports_nothing(source: Source) -> None:
    out = run(*NAMED, source=source.pk, check=True)
    assert "4 charts can be imported" in out
    assert nothing_saved()


# --- Subjects -------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_file_without_a_name_needs_a_subject(source: Source) -> None:
    message = refusal(BST, LMT, source=source.pk)
    assert f"{LMT.name}: no-name" in message
    assert nothing_saved()


@pytest.mark.django_db
def test_the_subject_option_names_every_file_s_subject(source: Source) -> None:
    run(LMT, BST, source=source.pk, subject="Synthetic Person Five")
    assert list(Subject.objects.values_list("label", flat=True)) == ["Synthetic Person Five"]
    assert Record.objects.filter(subject__label="Synthetic Person Five").count() == 2


@pytest.mark.django_db
def test_a_subject_option_too_long_for_its_label_is_refused(source: Source) -> None:
    # A file's own name fits (the format allows 100 UTF-16 code units); the option is checked,
    # since SQLite wouldn't enforce the label's 200 characters and PostgreSQL would refuse them
    # only while saving.
    with pytest.raises(
        CommandError, match="--subject: Ensure this value has at most 200 characters"
    ):
        run(BST, source=source.pk, subject="x" * 201)
    assert nothing_saved()
    run(BST, source=source.pk, subject="x" * 200)
    assert Subject.objects.get().label == "x" * 200


@pytest.mark.django_db
def test_a_name_that_s_already_a_subject_needs_add_to_existing(source: Source) -> None:
    existing = Subject.objects.create(label="Synthetic Person One")
    message = refusal(BST, source=source.pk)
    assert f"{BST.name}: existing-subject" in message
    assert not Record.objects.exists()
    run(BST, source=source.pk, add_to_existing=True)
    assert Record.objects.get().subject == existing
    assert Subject.objects.count() == 1


@pytest.mark.django_db
def test_files_of_a_batch_that_share_a_name_need_add_to_existing(
    source: Source, tmp_path: Path
) -> None:
    def later(document: dict[str, Any]) -> None:
        document["chart"]["local"] = "1987-06-15T04:40:00"

    other = edited(BST, tmp_path, "other-source.astrochart.json", later)
    message = refusal(BST, other, source=source.pk)
    assert f"{BST.name}: shared-name" in message and f"{other.name}: shared-name" in message
    assert nothing_saved()
    run(BST, other, source=source.pk, add_to_existing=True)
    assert Subject.objects.get().records.count() == 2


# --- Refusals -------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_batch_with_any_refused_file_imports_nothing(source: Source, tmp_path: Path) -> None:
    broken = tmp_path / "broken.astrochart.json"
    broken.write_text("{", encoding="utf-8")

    def newer(document: dict[str, Any]) -> None:
        document["version"] = 2

    later = edited(BST, tmp_path, "later.astrochart.json", newer)
    message = refusal(*NAMED, broken, later, source=source.pk)
    assert f"{broken.name}: malformed" in message
    assert f"{later.name}: unsupported-version" in message
    assert "2 of 6 files" in message
    assert nothing_saved()


@pytest.mark.django_db
def test_a_file_that_isn_t_utf_8_is_refused(source: Source, tmp_path: Path) -> None:
    latin = tmp_path / "latin.astrochart.json"
    latin.write_bytes(BST.read_text(encoding="utf-8").replace("One", "Øne").encode("latin-1"))
    assert f"{latin.name}: not-utf-8" in refusal(latin, source=source.pk)


@pytest.mark.django_db
def test_the_engine_s_refusal_refuses_the_batch(source: Source, tmp_path: Path) -> None:
    def seconds(document: dict[str, Any]) -> None:
        document["chart"]["local"] = "1987-06-15T04:25:30"

    finer = edited(BST, tmp_path, "finer.astrochart.json", seconds)
    message = refusal(finer, HALF_HOUR, source=source.pk, resolution="minute")
    assert f"{finer.name}: invalid-record" in message  # finer than a minute
    assert nothing_saved()


@pytest.mark.django_db
def test_the_same_file_can_t_be_imported_twice(source: Source, tmp_path: Path) -> None:
    run(BST, source=source.pk)
    again = refusal(BST, source=source.pk, add_to_existing=True)
    assert f"{BST.name}: duplicate" in again
    copy = tmp_path / "copy.astrochart.json"
    shutil.copyfile(HALF_HOUR, copy)
    twice = refusal(HALF_HOUR, copy, source=source.pk)
    assert f"{copy.name}: duplicate" in twice
    assert Record.objects.count() == 1


@pytest.mark.django_db
def test_the_database_keeps_each_file_once(source: Source) -> None:
    run(BST, source=source.pk)
    kept = ImportedChart.objects.get()
    record = Record.objects.create(
        subject=kept.record.subject,
        source=source,
        time_kind=Record.TimeKind.UTC,
        reading="2000-01-01T00:00:00+00:00",
        latitude=0.0,
        longitude=0.0,
    )
    with transaction.atomic(), pytest.raises(IntegrityError):
        ImportedChart.objects.create(
            record=record, text=kept.text, sha256=kept.sha256, file_name="again.astrochart.json"
        )


@pytest.mark.django_db
def test_a_failure_while_saving_keeps_nothing(
    source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    create = ImportedChart.objects.create
    calls = []

    def failing(**fields: Any) -> ImportedChart:
        calls.append(fields["file_name"])
        if len(calls) == 2:
            raise RuntimeError("the disk is full")
        return create(**fields)

    monkeypatch.setattr(ImportedChart.objects, "create", failing)
    with pytest.raises(RuntimeError, match="the disk is full"):
        run(BST, HALF_HOUR, source=source.pk)
    assert len(calls) == 2
    assert nothing_saved()


@pytest.mark.django_db
def test_the_source_and_dataset_must_exist(source: Source) -> None:
    with pytest.raises(CommandError, match="no source with id 999999"):
        run(BST, source=999_999)
    with pytest.raises(CommandError, match="no dataset named 'missing'"):
        run(BST, source=source.pk, dataset="missing")
    with pytest.raises(CommandError, match="--source"):
        run(BST)
    assert nothing_saved()


# --- The admin ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_an_imported_record_s_admin_page_shows_its_file(
    admin_client: Client, source: Source
) -> None:
    run(SOUTHERN, source=source.pk)
    record = Record.objects.get()
    response = admin_client.get(f"/admin/research_platform/record/{record.pk}/change/")
    assert response.status_code == 200
    text = response.content.decode()
    assert SOUTHERN.name in text and "screenshot" in text and "zone-label" in text


@pytest.mark.django_db
def test_imported_files_can_t_be_added_or_changed_in_the_admin(
    admin_client: Client, source: Source
) -> None:
    run(BST, SOUTHERN, HALF_HOUR, source=source.pk)  # two values each of via, site, UTC basis
    kept = ImportedChart.objects.get(file_name=BST.name)
    assert admin_client.get("/admin/research_platform/importedchart/add/").status_code == 403
    page = admin_client.get(f"/admin/research_platform/importedchart/{kept.pk}/change/")
    assert page.status_code == 200 and 'name="_save"' not in page.content.decode()
    listing = admin_client.get("/admin/research_platform/importedchart/").content.decode()
    for field in ("via", "site", "utc_basis"):  # the filters
        assert f'href="?{field}=' in listing, field
