"""Reading records and variable values from CSV, and checking every row before anything is saved.

One row per subject: the birth data, then a column per variable already defined. The time kind
is stated in each row, never guessed from which cells are filled:

- `utc`: `time` is the UTC reading;
- `zone`: `time` is the wall clock in `zone`, an IANA name; `utc_offset` may state its offset,
  which the engine then checks;
- `offset`: `time` is the wall clock at `utc_offset`, to the second if need be;
- `local-mean-time`: the birthplace's local mean time.

`time` is ISO 8601 without an offset, such as 1987-06-15T04:25. A variable's cell is one of its
categories by name (exactly), a number, or yes/no in any case; an empty cell is a missing
value. Each row is refused with the first reason found, by its line (the header is line 1):
birth data, then the subject, then its values. A column that's neither birth data nor a
variable refuses the whole file, so a typo can't drop a variable. Excel's UTF-8 byte order mark
is accepted; columns are separated by commas. A NUL character refuses the file: Python's csv
reads it as part of a cell (observed), and PostgreSQL can't store it in text, so it stopped the
import with a DataError instead of a refusal (observed).

`import_csv` and the upload page (`upload_views`) both read a file with `read_text` and check
it with `plan_import`, which says how to state that a subject is the same person in their own
words: the command's option, or the page's box.
"""

import csv
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction

from astro_research.chart import BirthRecord, InvalidBirthRecord, TimeResolution
from astro_research.civil_time import (
    CivilTime,
    CivilTimeError,
    LocalMeanTime,
    OffsetLocalTime,
    UtcTime,
    ZonedLocalTime,
)
from astro_research.geo import GeoPosition, InvalidCoordinates
from research_platform.models import (
    Category,
    Dataset,
    Observation,
    Record,
    Source,
    Subject,
    Variable,
)

REQUIRED = ("subject", "time", "time_kind", "latitude", "longitude")
OPTIONAL = (
    "zone",
    "utc_offset",
    "elevation_m",
    "time_resolution",
    "stated_uncertainty_s",
    "accept_pre_1970_tzdb",
)
TIME_KINDS = ("utc", "zone", "offset", "local-mean-time")
RESOLUTIONS = ("hour", "minute", "second")
_OFFSET = re.compile(r"([+-])(\d{2}):(\d{2})(?::(\d{2}))?")
_ENGINE_REFUSALS = (CivilTimeError, InvalidCoordinates, InvalidBirthRecord)


class FileRefused(ValueError):
    """The file as a whole can't be read; the message says why."""


class RowRefused(ValueError):
    """One row can't be imported; the message says why."""


@dataclass
class Value:
    variable: Variable
    category: Category | None = None
    number: float | None = None
    yes_no: bool | None = None


@dataclass
class PlannedRow:
    line: int
    label: str
    birth_record: BirthRecord
    subject: Subject | None  # an existing subject, or None for a new one
    values: list[Value] = field(default_factory=list)


@dataclass
class Plan:
    rows: list[PlannedRow]
    refused: dict[int, str]  # line: reason
    total: int
    variables: list[Variable] = field(default_factory=list)  # those with a column, in order

    @property
    def new_subjects(self) -> int:
        return len({row.label for row in self.rows if row.subject is None})

    @property
    def values(self) -> int:
        return sum(len(row.values) for row in self.rows)


def read_text(content: bytes) -> str:
    """A file's text, without Excel's byte order mark; raises FileRefused if it isn't UTF-8."""
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FileRefused("the file isn't UTF-8") from None


def counted(number: int, noun: str) -> str:
    """Such as "1 record" or "2 records"."""
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def plan_import(
    text: str, source: Source, *, add_to_existing: bool, same_person: str = "--add-to-existing"
) -> Plan:
    """Every row of the text checked; raises FileRefused if the file can't be read at all.
    `same_person` names how to say a subject is the same person, in a refusal that needs it."""
    lines = text.splitlines()
    for number, each in enumerate(lines, start=1):
        if "\x00" in each:
            raise FileRefused(f"the file has a NUL character, on line {number}")
    reader = csv.DictReader(lines)
    variables = {v.name: v for v in Variable.objects.prefetch_related("categories")}
    columns = list(reader.fieldnames or [])
    _check_columns(columns, variables)
    rows: list[PlannedRow] = []
    refused: dict[int, str] = {}
    first_lines: dict[str, int] = {}  # label: the line it's first on
    given: dict[tuple[str, str], int] = {}  # (label, variable): the line giving it
    total = 0
    for line, cells in enumerate(reader, start=2):
        total += 1
        try:
            planned = _row(
                line, cells, source, variables, add_to_existing, same_person, first_lines, given
            )
        except RowRefused as refusal:
            refused[line] = str(refusal)
            continue
        rows.append(planned)
    used = [variables[column] for column in columns if column in variables]
    return Plan(rows=rows, refused=refused, total=total, variables=used)


def save(plan: Plan, source: Source, dataset: Dataset | None) -> None:
    """The plan's subjects, records and values, all in one transaction."""
    with transaction.atomic():
        made: dict[str, Subject] = {}
        for row in plan.rows:
            subject = row.subject or made.get(row.label)
            if subject is None:
                subject = made[row.label] = Subject.objects.create(label=row.label)
            record = Record.from_birth_record(row.birth_record, subject=subject, source=source)
            record.full_clean()
            record.save()
            if dataset is not None:
                dataset.records.add(record)
            for value in row.values:
                observation = Observation(
                    variable=value.variable,
                    subject=subject,
                    source=source,
                    category=value.category,
                    number=value.number,
                    yes_no=value.yes_no,
                )
                observation.full_clean()
                observation.save()


def _check_columns(columns: list[str], variables: dict[str, Variable]) -> None:
    for column in REQUIRED:
        if column not in columns:
            raise FileRefused(f"missing column {column!r}")
    for column in columns:
        if column not in (*REQUIRED, *OPTIONAL) and column not in variables:
            names = ", ".join(sorted(variables)) or "none yet"
            raise FileRefused(
                f"unknown column {column!r}; the columns are the birth data's and the "
                f"variables' ({names})"
            )


def _row(
    line: int,
    raw: dict[str | None, object],
    source: Source,
    variables: dict[str, Variable],
    add_to_existing: bool,
    same_person: str,
    first_lines: dict[str, int],
    given: dict[tuple[str, str], int],
) -> PlannedRow:
    if None in raw:
        raise RowRefused("it has more cells than the header has columns")
    cells = {str(k): (v if isinstance(v, str) else "").strip() for k, v in raw.items()}
    label = _required(cells, "subject")
    try:  # its length, which SQLite wouldn't enforce and PostgreSQL would refuse while saving
        Subject(label=label).clean_fields()
    except ValidationError as error:
        raise RowRefused(f"subject: {'; '.join(error.messages)}") from error
    birth_record = _birth_record(cells, source)
    subject = Subject.objects.filter(label=label).first()
    if subject is not None and not add_to_existing:
        raise RowRefused(f"subject {label!r} already exists; {same_person} adds a record to it")
    if label in first_lines and not add_to_existing:
        raise RowRefused(
            f"subject {label!r} is also on line {first_lines[label]}; {same_person} says "
            "they're the same person"
        )
    values = []
    for name, variable in variables.items():
        if cells.get(name, ""):
            if (label, name) in given:
                raise RowRefused(
                    f"a value of {name} for {label!r} is also on line {given[(label, name)]}"
                )
            if subject is not None and subject.observations.filter(variable=variable).exists():
                raise RowRefused(f"subject {label!r} already has a value of {name}")
            values.append(_value(variable, cells[name]))
    first_lines.setdefault(label, line)
    for value in values:
        given[(label, value.variable.name)] = line
    return PlannedRow(
        line=line, label=label, birth_record=birth_record, subject=subject, values=values
    )


def _required(cells: dict[str, str], column: str) -> str:
    if not cells.get(column, ""):
        raise RowRefused(f"{column} is missing")
    return cells[column]


def _number(cells: dict[str, str], column: str) -> float | None:
    text = cells.get(column, "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        raise RowRefused(f"{column} {text!r} is not a number") from None


def _birth_record(cells: dict[str, str], source: Source) -> BirthRecord:
    kind = _required(cells, "time_kind")
    if kind not in TIME_KINDS:
        raise RowRefused(f"time_kind {kind!r} is not one of {', '.join(TIME_KINDS)}")
    local = _reading(_required(cells, "time"))
    zone, offset_text = cells.get("zone", ""), cells.get("utc_offset", "")
    if kind == "zone" and not zone:
        raise RowRefused("a zone row needs a zone")
    if kind != "zone" and zone:
        raise RowRefused(f"zone is only for zone rows, not {kind}")
    if kind == "offset" and not offset_text:
        raise RowRefused("an offset row needs a utc_offset")
    if kind in ("utc", "local-mean-time") and offset_text:
        raise RowRefused(f"utc_offset is only for zone and offset rows, not {kind}")
    offset = _offset(offset_text) if offset_text else None
    _required(cells, "latitude")
    _required(cells, "longitude")
    latitude, longitude = _number(cells, "latitude"), _number(cells, "longitude")
    elevation = _number(cells, "elevation_m")
    resolution = cells.get("time_resolution", "")
    if resolution and resolution not in RESOLUTIONS:
        raise RowRefused(f"time_resolution {resolution!r} is not one of {', '.join(RESOLUTIONS)}")
    uncertainty = _number(cells, "stated_uncertainty_s")
    if uncertainty is not None and not uncertainty >= 0:
        raise RowRefused("stated_uncertainty_s must be 0 or more seconds")
    accept = _accept(cells.get("accept_pre_1970_tzdb", ""), kind)
    assert latitude is not None and longitude is not None
    try:
        time: CivilTime
        if kind == "utc":
            time = UtcTime(utc=local.replace(tzinfo=UTC))
        elif kind == "zone":
            time = ZonedLocalTime(
                local=local, zone=zone, stated_utc_offset=offset, accept_pre_1970_tzdb=accept
            )
        elif kind == "offset":
            assert offset is not None
            time = OffsetLocalTime(local=local, utc_offset=offset)
        else:
            time = LocalMeanTime(local=local)
        place = (
            GeoPosition(latitude=latitude, longitude=longitude)
            if elevation is None
            else GeoPosition(latitude=latitude, longitude=longitude, elevation_m=elevation)
        )
        birth_record = BirthRecord(
            time=time,
            place=place,
            time_resolution=TimeResolution(resolution) if resolution else None,
            stated_uncertainty=None if uncertainty is None else timedelta(seconds=uncertainty),
        )
    except _ENGINE_REFUSALS as error:
        raise RowRefused(str(error)) from error
    _check_with_the_engine(birth_record, source)
    return birth_record


def _check_with_the_engine(birth_record: BirthRecord, source: Source) -> None:
    """The record checked as the admin checks it (`Record.clean()` asks the engine)."""
    record = Record.from_birth_record(birth_record, subject=Subject(label="-"), source=source)
    try:
        record.full_clean(exclude=["subject"])
    except ValidationError as error:
        raise RowRefused("; ".join(error.messages)) from error


def _reading(text: str) -> datetime:
    try:
        reading = datetime.fromisoformat(text)
    except ValueError:
        reading = None
    if reading is None or not ("T" in text or " " in text):
        raise RowRefused(
            f"time {text!r} is not an ISO 8601 date and time, such as 1987-06-15T04:25"
        )
    if reading.tzinfo is not None:
        raise RowRefused(f"time {text!r} has an offset; give it in utc_offset")
    return reading


def _offset(text: str) -> timedelta:
    found = _OFFSET.fullmatch(text)
    if found is None:
        raise RowRefused(f"utc_offset {text!r} is not like +01:00 or -05:30")
    sign, hours, minutes, seconds = found.groups()
    offset = timedelta(hours=int(hours), minutes=int(minutes), seconds=int(seconds or 0))
    return -offset if sign == "-" else offset


def _accept(text: str, kind: str) -> bool:
    if not text:
        return False
    if text.lower() not in ("yes", "no"):
        raise RowRefused(f"accept_pre_1970_tzdb {text!r} is not yes or no")
    if kind != "zone":
        raise RowRefused("accept_pre_1970_tzdb is only for zone rows")
    return text.lower() == "yes"


def _value(variable: Variable, text: str) -> Value:
    if variable.kind == Variable.Kind.CATEGORY:
        category = next((c for c in variable.categories.all() if c.name == text), None)
        if category is None:
            raise RowRefused(f"{text!r} is not one of {variable.name}'s categories")
        return Value(variable, category=category)
    if variable.kind == Variable.Kind.NUMBER:
        try:
            number = float(text)
        except ValueError:
            number = math.nan
        if not math.isfinite(number):
            raise RowRefused(f"{variable.name} {text!r} is not a number")
        return Value(variable, number=number)
    if text.lower() not in ("yes", "no"):
        raise RowRefused(f"{variable.name} {text!r} is not yes or no")
    return Value(variable, yes_no=text.lower() == "yes")
