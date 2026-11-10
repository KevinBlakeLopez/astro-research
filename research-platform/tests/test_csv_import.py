"""Importing records and variable values from CSV, all or nothing.

python manage.py import_csv FILE --source ID [--dataset NAME] [--add-to-existing] [--check]

One row per subject: the birth data, then a column per variable already defined. The time kind
is stated in each row (utc, zone, offset, local-mean-time); nothing is guessed from which cells
are filled. `time` is the wall-clock reading in ISO 8601, without an offset; `utc_offset` (like
+01:00, to the second for an offset row) is required for an offset row and may state a zone
row's offset. A variable's cell is a category's name, a number, or yes/no (in any case); an
empty cell is a missing value. The source cites the records and the values. A column that is
neither birth data nor a variable refuses the file, so a typo can't drop a variable.

Every row is checked as the admin checks it, the record by the engine. If any row is refused,
nothing is imported, and every refused row is listed by its line (the header is line 1). A
subject that already exists, or that's on two lines, is refused unless --add-to-existing says
it's the same person. Excel's UTF-8 byte order mark is accepted. Every row here is synthetic.
"""

import csv
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from django.core.management import CommandError, call_command

from astro_research.chart import BirthRecord, TimeResolution
from astro_research.civil_time import (
    CivilTime,
    LocalMeanTime,
    OffsetLocalTime,
    UtcTime,
    ZonedLocalTime,
)
from astro_research.geo import GeoPosition
from research_platform.models import (
    Category,
    Dataset,
    Observation,
    Record,
    Source,
    Subject,
    Variable,
)

BIRTH_COLUMNS = (
    "subject",
    "time",
    "time_kind",
    "zone",
    "utc_offset",
    "latitude",
    "longitude",
    "elevation_m",
    "time_resolution",
    "stated_uncertainty_s",
    "accept_pre_1970_tzdb",
)
PARIS = {"latitude": "48.8566", "longitude": "2.3522"}


def row(**cells: str) -> dict[str, str]:
    """A zone row in Paris, with `cells` changed."""
    return {
        "subject": "subject-a",
        "time": "1990-03-21T12:00",
        "time_kind": "zone",
        "zone": "Europe/Paris",
        **PARIS,
        **cells,
    }


def write(directory: Path, rows: list[dict[str, str]], *, encoding: str = "utf-8") -> Path:
    columns = [c for c in BIRTH_COLUMNS if any(c in r for r in rows)]
    columns += sorted({c for r in rows for c in r} - set(BIRTH_COLUMNS))
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    path = directory / "subjects.csv"
    path.write_bytes(buffer.getvalue().encode(encoding))
    return path


def run(path: Path, source: Source, **options: Any) -> str:
    out = io.StringIO()
    call_command("import_csv", str(path), source=source.pk, stdout=out, **options)
    return out.getvalue().strip()


def refusal(path: Path, source: Source, **options: Any) -> str:
    with pytest.raises(CommandError) as raised:
        run(path, source, **options)
    message = str(raised.value)
    assert message.startswith("nothing was imported")
    return message


def nothing_saved() -> bool:
    return not (Record.objects.exists() or Observation.objects.exists() or Subject.objects.exists())


@pytest.fixture
def variables() -> dict[str, Variable]:
    profession = Variable.objects.create(name="profession", kind=Variable.Kind.CATEGORY)
    for name in ("athlete", "musician"):
        Category.objects.create(variable=profession, name=name)
    return {
        "profession": profession,
        "height": Variable.objects.create(name="height", kind=Variable.Kind.NUMBER, unit="cm"),
        "left-handed": Variable.objects.create(name="left-handed", kind=Variable.Kind.YES_NO),
    }


# --- What a row becomes ---------------------------------------------------------------------


@pytest.mark.django_db
def test_each_time_kind_becomes_the_engine_s_record(tmp_path: Path, source: Source) -> None:
    place = GeoPosition(latitude=48.8566, longitude=2.3522)
    local = datetime(1990, 3, 21, 12, 0)
    rows = [
        row(subject="utc", time_kind="utc", zone=""),
        row(subject="zone"),
        row(subject="zone-stated", utc_offset="+01:00"),
        row(subject="offset", time_kind="offset", zone="", utc_offset="+01:00"),
        row(subject="seconds", time_kind="offset", zone="", utc_offset="+00:09:21"),
        row(subject="west", time_kind="offset", zone="", utc_offset="-05:30"),
        row(subject="mean", time_kind="local-mean-time", zone=""),
    ]
    run(write(tmp_path, rows), source)
    expected: dict[str, CivilTime] = {
        "utc": UtcTime(utc=local.replace(tzinfo=UTC)),
        "zone": ZonedLocalTime(local=local, zone="Europe/Paris"),
        "zone-stated": ZonedLocalTime(
            local=local, zone="Europe/Paris", stated_utc_offset=timedelta(hours=1)
        ),
        "offset": OffsetLocalTime(local=local, utc_offset=timedelta(hours=1)),
        "seconds": OffsetLocalTime(local=local, utc_offset=timedelta(minutes=9, seconds=21)),
        "west": OffsetLocalTime(local=local, utc_offset=-timedelta(hours=5, minutes=30)),
        "mean": LocalMeanTime(local=local),
    }
    for label, time in expected.items():
        record = Record.objects.get(subject__label=label)
        assert record.birth_record() == BirthRecord(time=time, place=place), label
        assert record.source == source


@pytest.mark.django_db
def test_the_optional_birth_data_columns(tmp_path: Path, source: Source) -> None:
    rows = [
        row(
            time="1950-06-01T08:30",
            elevation_m="35",
            time_resolution="minute",
            stated_uncertainty_s="900",
            accept_pre_1970_tzdb="yes",
        )
    ]
    run(write(tmp_path, rows), source)
    birth = Record.objects.get().birth_record()
    assert birth.place.elevation_m == 35.0
    assert birth.time_resolution == TimeResolution.MINUTE
    assert birth.stated_uncertainty == timedelta(minutes=15)
    assert isinstance(birth.time, ZonedLocalTime) and birth.time.accept_pre_1970_tzdb is True


@pytest.mark.django_db
def test_values_come_in_as_each_variable_s_kind(
    tmp_path: Path, source: Source, variables: dict[str, Variable]
) -> None:
    rows = [
        row(subject="a", profession="athlete", height="181.5", **{"left-handed": "Yes"}),
        row(subject="b", profession="", height="", **{"left-handed": "no"}),
    ]
    run(write(tmp_path, rows), source)
    values = {(o.subject.label, o.variable.name): o.value for o in Observation.objects.all()}
    assert values == {
        ("a", "profession"): "athlete",
        ("a", "height"): 181.5,
        ("a", "left-handed"): True,
        ("b", "left-handed"): False,  # b's empty cells are missing values
    }
    assert {o.source for o in Observation.objects.all()} == {source}


@pytest.mark.django_db
def test_the_output_counts_what_was_imported(
    tmp_path: Path, source: Source, variables: dict[str, Variable]
) -> None:
    rows = [row(subject="a", profession="athlete"), row(subject="b", height="170")]
    output = run(write(tmp_path, rows), source)
    assert output == "imported 2 records: 2 new subjects, 2 values"
    one = run(write(tmp_path, [row(subject="c")]), source)
    assert one == "imported 1 record: 1 new subject, 0 values"


@pytest.mark.django_db
def test_check_imports_nothing(
    tmp_path: Path, source: Source, variables: dict[str, Variable]
) -> None:
    rows = [row(subject="a", profession="athlete")]
    output = run(write(tmp_path, rows), source, check=True)
    assert output == "1 record can be imported, with 1 value; nothing was saved"
    assert nothing_saved()


@pytest.mark.django_db
def test_the_dataset_option_adds_the_records(tmp_path: Path, source: Source) -> None:
    dataset = Dataset.objects.create(name="demo")
    run(write(tmp_path, [row(subject="a"), row(subject="b")]), source, dataset="demo")
    assert sorted(r.subject.label for r in dataset.records.all()) == ["a", "b"]


@pytest.mark.django_db
def test_excel_s_byte_order_mark_is_accepted(tmp_path: Path, source: Source) -> None:
    run(write(tmp_path, [row()], encoding="utf-8-sig"), source)
    assert Record.objects.get().subject.label == "subject-a"


# --- Refused rows ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("cells", "reason"),
    [
        ({"latitude": ""}, "latitude is missing"),
        ({"subject": ""}, "subject is missing"),
        ({"subject": "x" * 201}, "subject: Ensure this value has at most 200 characters"),
        (
            {"time_kind": "local"},
            "time_kind 'local' is not one of utc, zone, offset, local-mean-time",
        ),
        ({"time": "21/03/1990 12:00"}, "time '21/03/1990 12:00' is not an ISO 8601 date and time"),
        ({"time": "1990-03-21"}, "time '1990-03-21' is not an ISO 8601 date and time"),  # no time
        (
            {"time": "1990-03-21T12:00+01:00"},
            "time '1990-03-21T12:00+01:00' has an offset; give it in utc_offset",
        ),
        ({"zone": ""}, "a zone row needs a zone"),
        ({"time_kind": "offset"}, "zone is only for zone rows, not offset"),
        ({"time_kind": "offset", "zone": ""}, "an offset row needs a utc_offset"),
        (
            {"time_kind": "utc", "zone": "", "utc_offset": "+01:00"},
            "utc_offset is only for zone and offset rows, not utc",
        ),
        ({"utc_offset": "1 hour"}, "utc_offset '1 hour' is not like +01:00 or -05:30"),
        ({"longitude": "east"}, "longitude 'east' is not a number"),
        ({"time_resolution": "day"}, "time_resolution 'day' is not one of hour, minute, second"),
        ({"accept_pre_1970_tzdb": "sure"}, "accept_pre_1970_tzdb 'sure' is not yes or no"),
        (
            {
                "time_kind": "offset",
                "zone": "",
                "utc_offset": "+01:00",
                "accept_pre_1970_tzdb": "yes",
            },
            "accept_pre_1970_tzdb is only for zone rows",
        ),
        ({"stated_uncertainty_s": "-60"}, "stated_uncertainty_s must be 0 or more seconds"),
        ({"latitude": "95"}, "latitude"),  # the engine's reason
        ({"zone": "Mars/Olympus"}, "Mars/Olympus"),  # the engine's reason
        ({"time": "1990-03-21T12:30", "time_resolution": "hour"}, "resolution"),  # the engine's
    ],
)
def test_a_row_the_import_can_t_take_is_refused_by_its_line(
    tmp_path: Path, source: Source, cells: dict[str, str], reason: str
) -> None:
    message = refusal(write(tmp_path, [row(**cells)]), source)
    assert "1 of 1 rows refused" in message
    line = next(part for part in message.splitlines() if part.strip().startswith("line 2:"))
    assert reason in line
    assert nothing_saved()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("cells", "reason"),
    [
        ({"profession": "Athlete"}, "'Athlete' is not one of profession's categories"),
        ({"height": "tall"}, "height 'tall' is not a number"),
        ({"height": "inf"}, "height 'inf' is not a number"),
        ({"left-handed": "maybe"}, "left-handed 'maybe' is not yes or no"),
    ],
)
def test_a_value_its_variable_can_t_take_refuses_the_row(
    tmp_path: Path,
    source: Source,
    variables: dict[str, Variable],
    cells: dict[str, str],
    reason: str,
) -> None:
    message = refusal(write(tmp_path, [row(**cells)]), source)
    assert f"line 2: {reason}" in message
    assert nothing_saved()


@pytest.mark.django_db
def test_every_refused_row_is_listed_and_nothing_is_imported(
    tmp_path: Path, source: Source, variables: dict[str, Variable]
) -> None:
    rows = [row(subject="a"), row(subject="b", latitude=""), row(subject="c", height="tall")]
    message = refusal(write(tmp_path, rows), source)
    assert "2 of 3 rows refused" in message
    assert "line 3: latitude is missing" in message
    assert "line 4: height 'tall' is not a number" in message
    assert "line 2:" not in message
    assert nothing_saved()


# --- Refused files --------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_column_that_is_neither_birth_data_nor_a_variable_refuses_the_file(
    tmp_path: Path, source: Source, variables: dict[str, Variable]
) -> None:
    message = refusal(write(tmp_path, [row(professio="athlete")]), source)
    assert "unknown column 'professio'" in message
    assert "height, left-handed, profession" in message  # the variables there are
    assert nothing_saved()


@pytest.mark.django_db
@pytest.mark.parametrize("column", ["subject", "time", "time_kind", "latitude", "longitude"])
def test_a_missing_required_column_refuses_the_file(
    tmp_path: Path, source: Source, column: str
) -> None:
    cells = row()
    del cells[column]
    assert f"missing column {column!r}" in refusal(write(tmp_path, [cells]), source)


@pytest.mark.django_db
def test_a_file_that_isn_t_utf_8_is_refused(tmp_path: Path, source: Source) -> None:
    path = write(tmp_path, [row(subject="Zoë")], encoding="latin-1")
    assert "the file isn't UTF-8" in refusal(path, source)


@pytest.mark.django_db
def test_the_source_and_dataset_must_exist(tmp_path: Path, source: Source) -> None:
    path = write(tmp_path, [row()])
    with pytest.raises(CommandError, match="no source with id 999999"):
        call_command("import_csv", str(path), source=999_999)
    with pytest.raises(CommandError, match="no dataset named 'missing'"):
        run(path, source, dataset="missing")
    assert nothing_saved()


# --- Subjects already known -----------------------------------------------------------------


@pytest.mark.django_db
def test_a_subject_that_already_exists_needs_add_to_existing(
    tmp_path: Path, source: Source, variables: dict[str, Variable]
) -> None:
    known = Subject.objects.create(label="subject-a")
    path = write(tmp_path, [row(profession="athlete")])
    message = refusal(path, source)
    assert (
        "line 2: subject 'subject-a' already exists; --add-to-existing adds a record to it"
        in message
    )
    assert run(path, source, add_to_existing=True) == "imported 1 record: 0 new subjects, 1 value"
    assert Record.objects.get().subject == known
    assert Subject.objects.count() == 1


@pytest.mark.django_db
def test_an_existing_value_isn_t_overwritten(
    tmp_path: Path, source: Source, variables: dict[str, Variable]
) -> None:
    known = Subject.objects.create(label="subject-a")
    Observation.objects.create(
        variable=variables["profession"],
        subject=known,
        source=source,
        category=Category.objects.get(name="musician"),
    )
    message = refusal(write(tmp_path, [row(profession="athlete")]), source, add_to_existing=True)
    assert "line 2: subject 'subject-a' already has a value of profession" in message
    assert not Record.objects.exists()


@pytest.mark.django_db
def test_a_subject_on_two_lines_needs_add_to_existing(tmp_path: Path, source: Source) -> None:
    path = write(tmp_path, [row(), row(time="1990-03-21T13:00")])
    message = refusal(path, source)
    also = "is also on line 2; --add-to-existing says they're the same person"
    assert f"line 3: subject 'subject-a' {also}" in message
    assert run(path, source, add_to_existing=True) == "imported 2 records: 1 new subject, 0 values"
    assert Record.objects.filter(subject__label="subject-a").count() == 2


@pytest.mark.django_db
def test_two_lines_can_t_give_one_subject_two_values_of_a_variable(
    tmp_path: Path, source: Source, variables: dict[str, Variable]
) -> None:
    rows = [row(profession="athlete"), row(time="1990-03-21T13:00", profession="musician")]
    message = refusal(write(tmp_path, rows), source, add_to_existing=True)
    assert "line 3: a value of profession for 'subject-a' is also on line 2" in message
    assert nothing_saved()


@pytest.mark.django_db
def test_a_failure_while_saving_keeps_nothing(
    tmp_path: Path, source: Source, variables: dict[str, Variable], monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the disk is full")

    monkeypatch.setattr(Observation, "save", failing)
    with pytest.raises(RuntimeError, match="the disk is full"):
        run(write(tmp_path, [row(subject="a"), row(subject="b", profession="athlete")]), source)
    assert nothing_saved()
