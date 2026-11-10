"""The import page: a CSV file uploaded to be checked, shown as it was read, then imported.

`csv_import` reads and checks the file as `import_csv` does. Uploading only checks: a refused
file or row is shown with every reason (422), and nothing is kept or changed. A file that passes
waits as its account's `PendingImport`, replacing any before it. Its page shows each row as it
was read, with the UTC its birth time resolves to, so that a mistake no check can see, such as
latitude and longitude swapped or the wrong zone, can be seen. It's checked again whenever it's
shown or imported. Importing (a POST) saves every row or none, deletes the waiting file, and
opens the dataset's page, or the records page, saying what was imported. A waiting file is its
account's alone: another account's isn't found.
"""

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import assert_never

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from astro_research.chart import BirthRecord
from astro_research.civil_time import (
    CivilTime,
    CivilTimeError,
    LocalMeanTime,
    OffsetLocalTime,
    OffsetSource,
    ResolvedInstant,
    UtcTime,
    ZonedLocalTime,
    resolve,
)
from research_platform import csv_import
from research_platform.csv_import import OPTIONAL, REQUIRED, FileRefused, Plan, Value, counted
from research_platform.models import Dataset, PendingImport, Source, Variable

SAME_PERSON = 'ticking "Same person"'
UPLOAD = "research_platform/csv_import.html"
WAITING = "research_platform/pending_import.html"
OFFSET_SOURCES = {
    OffsetSource.UTC_GIVEN: "UTC given",
    OffsetSource.TZDB: "the time-zone database",
    OffsetSource.STATED: "as stated",
    OffsetSource.LOCAL_MEAN_TIME: "local mean time",
}


class UploadForm(forms.Form):
    file = forms.FileField(label="CSV file")
    source = forms.ModelChoiceField(
        queryset=Source.objects.order_by("pk"),
        help_text="The source every record and value cites.",
    )
    dataset = forms.ModelChoiceField(
        queryset=Dataset.objects.order_by("name"),
        required=False,
        help_text="A dataset to add the records to, if you like.",
    )
    add_to_existing = forms.BooleanField(
        required=False,
        label="Same person",
        help_text="A subject already here, or on two lines, is the same person: each row adds "
        "a record to it.",
    )


@dataclass(frozen=True)
class ShownRow:
    """A row of a waiting file as it was read."""

    line: int
    subject: str  # the label, and whether it's new
    time: str  # as given
    utc: str  # what it resolves to, or why it doesn't
    offset: str
    latitude: str
    longitude: str
    values: list[tuple[str, str]]  # (variable, value) for each variable with a column


@login_required
def upload(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return render(request, UPLOAD, _upload_context(request, UploadForm()))
    form = UploadForm(request.POST, request.FILES)
    if not form.is_valid():
        return render(request, UPLOAD, _upload_context(request, form), status=400)
    uploaded = form.cleaned_data["file"]
    source, dataset = form.cleaned_data["source"], form.cleaned_data["dataset"]
    add_to_existing = form.cleaned_data["add_to_existing"]
    try:
        text = csv_import.read_text(uploaded.read())
        plan = csv_import.plan_import(
            text, source, add_to_existing=add_to_existing, same_person=SAME_PERSON
        )
    except FileRefused as error:
        context = _upload_context(request, form) | {"refusal": f"Nothing was imported: {error}"}
        return render(request, UPLOAD, context, status=422)
    if plan.refused:
        context = _upload_context(request, form) | _refusals(plan, "Nothing was imported")
        return render(request, UPLOAD, context, status=422)
    with transaction.atomic():
        PendingImport.objects.filter(user=_account(request)).delete()
        pending = PendingImport.objects.create(
            user=_account(request),
            file_name=uploaded.name or "",
            text=text,
            source=source,
            dataset=dataset,
            add_to_existing=add_to_existing,
        )
    return redirect("pending-import", pk=pending.pk)


def _upload_context(request: HttpRequest, form: UploadForm) -> dict[str, object]:
    return {
        "form": form,
        "sources": Source.objects.exists(),
        "variables": sorted(Variable.objects.prefetch_related("categories"), key=_name),
        "waiting": PendingImport.objects.filter(user=_account(request)).first(),
    }


def _account(request: HttpRequest) -> User:
    user = request.user
    assert isinstance(user, User)  # login_required lets no one else in
    return user


def _name(variable: Variable) -> str:
    return variable.name


def _refusals(plan: Plan, heading: str) -> dict[str, object]:
    return {
        "refusal": f"{heading}: {len(plan.refused)} of {plan.total} rows refused",
        "refused_lines": list(plan.refused.items()),  # in line order
    }


@login_required
def blank_template(request: HttpRequest) -> HttpResponse:
    """A CSV with every column as its header row; the byte order mark tells Excel it's UTF-8."""
    names = sorted(Variable.objects.values_list("name", flat=True))
    buffer = io.StringIO()
    csv.writer(buffer).writerow([*REQUIRED, *OPTIONAL, *names])  # RFC 4180: \r\n
    content = ("\ufeff" + buffer.getvalue()).encode("utf-8")
    response = HttpResponse(content, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="import-template.csv"'
    return response


@login_required
def waiting(request: HttpRequest, pk: int) -> HttpResponse:
    pending = _waiting(request, pk)
    context, plan = _checked(pending)
    return render(request, WAITING, context, status=422 if plan is None else 200)


@login_required
@require_POST
def save(request: HttpRequest, pk: int) -> HttpResponse:
    pending = _waiting(request, pk)
    context, plan = _checked(pending)
    if plan is None:
        return render(request, WAITING, context, status=422)
    with transaction.atomic():
        csv_import.save(plan, pending.source, pending.dataset)
        pending.delete()
    records, values = counted(len(plan.rows), "record"), counted(plan.values, "value")
    subjects = counted(plan.new_subjects, "new subject")
    messages.success(request, f"Imported {records}: {subjects}, {values}")
    if pending.dataset is not None:
        return redirect("dataset", pk=pending.dataset.pk)
    return redirect("records")


def _waiting(request: HttpRequest, pk: int) -> PendingImport:
    """The account's own waiting file; another account's isn't found."""
    files = PendingImport.objects.select_related("source", "dataset")
    return get_object_or_404(files, pk=pk, user=_account(request))


def _checked(pending: PendingImport) -> tuple[dict[str, object], Plan | None]:
    """The waiting file checked again: its page's context, and its plan if nothing's refused."""
    context: dict[str, object] = {"pending": pending}
    try:
        plan = csv_import.plan_import(
            pending.text,
            pending.source,
            add_to_existing=pending.add_to_existing,
            same_person=SAME_PERSON,
        )
    except FileRefused as error:
        return context | {"refusal": f"This file can't be imported now: {error}"}, None
    if plan.refused:
        return context | _refusals(plan, "This file can't be imported now"), None
    records = counted(len(plan.rows), "record")
    subjects, values = counted(plan.new_subjects, "new subject"), counted(plan.values, "value")
    context |= {
        "summary": f"{records} can be imported: {subjects}, {values}",
        "variables": plan.variables,
        "rows": _shown(plan),
    }
    return context, plan


def _shown(plan: Plan) -> list[ShownRow]:
    first_lines: dict[str, int] = {}
    shown = []
    for row in plan.rows:
        if row.subject is not None:
            status = "already here"
        elif row.label in first_lines:
            status = f"as on line {first_lines[row.label]}"
        else:
            status = "new"
        first_lines.setdefault(row.label, row.line)
        record = row.birth_record
        resolved, utc = _utc_of(record)
        given = {value.variable.pk: _value(value) for value in row.values}
        shown.append(
            ShownRow(
                line=row.line,
                subject=f"{row.label} ({status})",
                time=_as_given(record.time),
                utc=utc,
                offset=_offset_of(resolved),
                latitude=str(record.place.latitude),
                longitude=str(record.place.longitude),
                values=[(v.name, given.get(v.pk, "")) for v in plan.variables],
            )
        )
    return shown


def _utc_of(record: BirthRecord) -> tuple[ResolvedInstant | None, str]:
    try:
        resolved = resolve(record.time, record.place)
    except CivilTimeError as error:  # such as a time that happens twice; a run refuses it
        return None, f"none: {error}"
    return resolved, f"{_reading(resolved.utc.replace(tzinfo=None))} UTC"


def _reading(moment: datetime) -> str:
    return moment.isoformat(sep=" ")


def _as_given(time: CivilTime) -> str:
    match time:
        case UtcTime():
            return f"{_reading(time.utc.replace(tzinfo=None))} UTC"
        case ZonedLocalTime():
            stated = time.stated_utc_offset
            also = "" if stated is None else f", stated as {_offset(stated)}"
            return f"{_reading(time.local)} in {time.zone}{also}"
        case OffsetLocalTime():
            return f"{_reading(time.local)} at {_offset(time.utc_offset)}"
        case LocalMeanTime():
            return f"{_reading(time.local)} local mean time"
        case _:
            assert_never(time)


def _offset_of(resolved: ResolvedInstant | None) -> str:
    if resolved is None:
        return ""
    where = OFFSET_SOURCES[resolved.offset_source]
    if resolved.zone_abbreviation:
        where = f"{resolved.zone_abbreviation}, {where}"
    return f"{_offset(resolved.utc_offset)} ({where})"


def _offset(offset: timedelta) -> str:
    """Such as +01:00, -05:30, or +00:09:24.528 to the second and its fraction."""
    sign = "-" if offset < timedelta(0) else "+"
    hours, rest = divmod(abs(offset), timedelta(hours=1))
    minutes, rest = divmod(rest, timedelta(minutes=1))
    text = f"{sign}{hours:02d}:{minutes:02d}"
    if rest:
        text += f":{rest.seconds:02d}"
        if rest.microseconds:
            text += f".{rest.microseconds:06d}".rstrip("0")
    return text


def _value(value: Value) -> str:
    if value.category is not None:
        return value.category.name
    if value.number is not None:
        return f"{value.number!r} {value.variable.unit}".strip()
    return "yes" if value.yes_no else "no"
