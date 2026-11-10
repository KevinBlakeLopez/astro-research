"""Importing a CSV file from a page: upload it to check it, look at what it will become, then
import it.

- **The upload page** (`/import/`) takes the file, the source every row cites, a dataset to add
  the records to (optional), and a "same person" box, `import_csv`'s --add-to-existing. It
  lists the columns, the variables' included, offers a blank CSV with them as its header, and
  says to save from Excel as CSV UTF-8. With no source yet, it says to add one in the admin.
- **Uploading only checks.** A refused file or row is shown with every reason, by line, with a
  422, and nothing is kept or changed. A file that passes waits, one per account (a new upload
  replaces it), and its page shows the counts and each row as it was read: the subject and
  whether it's new, the UTC its birth time resolves to and the offset used, the place, and the
  values. It's checked again whenever it's shown or imported, since a subject or a variable may
  have changed in the meantime.
- **Import** (a POST) saves every row or none, deletes the waiting file, and opens the
  dataset's page, or the records page, saying what was imported.
- **A waiting file is its account's alone:** another account's isn't found.

Markup the tests rely on: the waiting file's rows carry data-line; their cells data-cell
("subject", "utc", "offset", "latitude", "longitude") or data-variable (the variable's name).
Every row here is synthetic.
"""

import csv
import html
import io
import re
from typing import TYPE_CHECKING, Any

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import resolve, reverse

from research_platform.csv_import import OPTIONAL, REQUIRED
from research_platform.models import (
    Category,
    Dataset,
    Observation,
    PendingImport,
    Record,
    Source,
    Subject,
    Variable,
)

if TYPE_CHECKING:  # the test client's response, as django-stubs names it
    from django.test.client import _MonkeyPatchedWSGIResponse as Response

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


def csv_file(
    rows: list[dict[str, str]], *, name: str = "subjects.csv", encoding: str = "utf-8"
) -> SimpleUploadedFile:
    columns = list(dict.fromkeys(column for each in rows for column in each))
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return SimpleUploadedFile(name, buffer.getvalue().encode(encoding), content_type="text/csv")


def upload(
    client: Client, source: Source, file: list[dict[str, str]] | SimpleUploadedFile, **fields: Any
) -> "Response":
    if isinstance(file, list):
        file = csv_file(file)
    return client.post(reverse("csv-import"), {"file": file, "source": source.pk, **fields})


def waiting(response: "Response") -> str:
    """The waiting file's address, which a file that passes its check is sent to."""
    assert response.status_code == 302, html.unescape(response.content.decode())[:800]
    address = str(response["Location"])
    assert resolve(address).url_name == "pending-import"
    return address


def save_address(address: str) -> str:
    return reverse("pending-import-save", args=[resolve(address).kwargs["pk"]])


def page(client: Client, address: str, status: int = 200) -> str:
    response = client.get(address)
    assert response.status_code == status, html.unescape(response.content.decode())[:800]
    return html.unescape(response.content.decode())


def text_of(response: "Response") -> str:
    return html.unescape(response.content.decode())


def select(text: str, name: str) -> str:
    found = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', text, re.S)
    assert found is not None, name
    return found.group(1)


def line(text: str, number: int) -> dict[str, str]:
    """A waiting file's row by its line: {"subject": ..., "variable:profession": ...}."""
    found = re.search(rf'<tr data-line="{number}">(.*?)</tr>', text, re.S)
    assert found is not None, f"no row for line {number}"
    cells = {}
    for kind, name, value in re.findall(
        r'<td data-(cell|variable)="([^"]+)"[^>]*>(.*?)</td>', found.group(1), re.S
    ):
        key = name if kind == "cell" else f"variable:{name}"
        cells[key] = " ".join(re.sub(r"<[^>]+>", " ", value).split())
    return cells


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


TWO_ROWS = [
    row(subject="subject-a", profession="athlete", height="172"),
    row(subject="subject-b", time_kind="utc", zone="", **{"left-handed": "yes"}),
]


# --- The upload page ------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_upload_page_lists_the_columns_and_offers_a_blank_template(
    admin_client: Client, source: Source, variables: dict[str, Variable]
) -> None:
    text = page(admin_client, reverse("csv-import"))
    for column in (*REQUIRED, *OPTIONAL, *variables):
        assert f"<code>{column}</code>" in text, column
    assert f'href="{reverse("csv-import-template")}"' in text
    assert "CSV UTF-8" in text
    assert 'enctype="multipart/form-data"' in text and 'type="file"' in text
    sources = select(text, "source")
    assert f'value="{source.pk}"' in sources and source.citation in sources


@pytest.mark.django_db
def test_with_no_source_yet_the_upload_page_says_to_add_one(admin_client: Client) -> None:
    text = page(admin_client, reverse("csv-import"))
    assert f'href="{reverse("admin:research_platform_source_add")}"' in text
    assert 'type="file"' not in text


@pytest.mark.django_db
def test_the_header_links_to_the_upload_page(admin_client: Client) -> None:
    header = re.search(r"<header>.*?</header>", page(admin_client, reverse("datasets")), re.S)
    assert header is not None
    assert f'href="{reverse("csv-import")}"' in header.group(0)


@pytest.mark.django_db
def test_the_blank_template_has_every_column_and_imports_once_filled(
    admin_client: Client, source: Source, variables: dict[str, Variable]
) -> None:
    response = admin_client.get(reverse("csv-import-template"))
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert response["Content-Disposition"] == 'attachment; filename="import-template.csv"'
    content = response.content
    assert content.startswith(b"\xef\xbb\xbf")  # so that Excel opens it as UTF-8
    header = content.decode("utf-8-sig")
    columns = [*REQUIRED, *OPTIONAL, *sorted(variables)]
    assert header == ",".join(columns) + "\r\n"

    cells = row(profession="athlete", height="172", **{"left-handed": "no"})
    filled = content + (",".join(cells.get(c, "") for c in columns) + "\r\n").encode()
    address = waiting(upload(admin_client, source, SimpleUploadedFile("filled.csv", filled)))
    assert admin_client.post(save_address(address)).status_code == 302
    assert Record.objects.get().subject.label == "subject-a"
    assert Observation.objects.count() == 3


# --- Uploading checks -----------------------------------------------------------------------


@pytest.mark.django_db
def test_uploading_only_checks_and_the_waiting_file_shows_each_row_as_read(
    admin_client: Client, source: Source, variables: dict[str, Variable]
) -> None:
    address = waiting(upload(admin_client, source, TWO_ROWS))
    assert nothing_saved()
    assert PendingImport.objects.count() == 1

    text = page(admin_client, address)
    assert "subjects.csv" in text and source.citation in text
    assert "2 records can be imported: 2 new subjects, 3 values" in text
    paris = line(text, 2)
    assert paris["subject"] == "subject-a (new)"
    assert paris["utc"] == "1990-03-21 11:00:00 UTC"  # noon in Paris, CET
    assert paris["offset"] == "+01:00 (CET, the time-zone database)"
    assert (paris["latitude"], paris["longitude"]) == ("48.8566", "2.3522")
    assert paris["variable:profession"] == "athlete"
    assert paris["variable:height"] == "172.0 cm"
    utc = line(text, 3)
    assert utc["utc"] == "1990-03-21 12:00:00 UTC"
    assert utc["offset"] == "+00:00 (UTC given)"
    assert utc["variable:left-handed"] == "yes"
    assert f'action="{save_address(address)}"' in text


@pytest.mark.django_db
def test_each_time_kind_is_shown_as_given_with_its_utc_and_offset(
    admin_client: Client, source: Source, variables: dict[str, Variable]
) -> None:
    rows = [
        row(subject="utc", time_kind="utc", zone="", **{"left-handed": "no"}),
        row(subject="stated", utc_offset="+01:00"),
        row(subject="west", time_kind="offset", zone="", utc_offset="-05:30"),
        row(subject="mean", time_kind="local-mean-time", zone=""),
    ]
    text = page(admin_client, waiting(upload(admin_client, source, rows)))
    shown = {line(text, n)["subject"]: line(text, n) for n in range(2, 6)}
    assert shown["utc (new)"]["time"] == "1990-03-21 12:00:00 UTC"
    assert shown["utc (new)"]["variable:left-handed"] == "no"
    # Only the variables the file has a column for.
    assert [k for k in shown["utc (new)"] if k.startswith("variable:")] == ["variable:left-handed"]
    stated = shown["stated (new)"]
    assert stated["time"] == "1990-03-21 12:00:00 in Europe/Paris, stated as +01:00"
    assert stated["utc"] == "1990-03-21 11:00:00 UTC"
    west = shown["west (new)"]
    assert west["time"] == "1990-03-21 12:00:00 at -05:30"
    assert (west["utc"], west["offset"]) == ("1990-03-21 17:30:00 UTC", "-05:30 (as stated)")
    mean = shown["mean (new)"]  # Paris is 2.3522 degrees east: 564.528 seconds ahead
    assert mean["time"] == "1990-03-21 12:00:00 local mean time"
    assert mean["utc"] == "1990-03-21 11:50:35.472000 UTC"
    assert mean["offset"] == "+00:09:24.528 (local mean time)"


@pytest.mark.django_db
def test_a_time_with_no_single_utc_is_shown_as_such_and_still_imported(
    admin_client: Client, source: Source
) -> None:
    # 01:30 happens twice in London as British Summer Time ends. The record is kept, as the
    # command keeps it, and a run records the engine's refusal of its chart.
    rows = [row(time="1987-10-25T01:30", zone="Europe/London")]
    address = waiting(upload(admin_client, source, rows))
    cells = line(page(admin_client, address), 2)
    assert cells["utc"].startswith("none: ") and "1987-10-25" in cells["utc"]
    assert cells["offset"] == ""
    assert cells["time"] == "1987-10-25 01:30:00 in Europe/London"
    assert admin_client.post(save_address(address)).status_code == 302
    assert Record.objects.count() == 1


@pytest.mark.django_db
def test_the_same_person_box_adds_records_to_subjects_already_here(
    admin_client: Client, source: Source
) -> None:
    Subject.objects.create(label="subject-a")
    refused = upload(admin_client, source, [row()])
    assert refused.status_code == 422
    assert "line 2: subject 'subject-a' already exists" in text_of(refused)
    assert "--add-to-existing" not in text_of(refused)  # the page's words, not the command's
    twice = upload(admin_client, source, [row(subject="subject-b"), row(subject="subject-b")])
    assert twice.status_code == 422
    assert "line 3: subject 'subject-b' is also on line 2" in text_of(twice)
    assert "--add-to-existing" not in text_of(twice)

    address = waiting(upload(admin_client, source, [row()], add_to_existing="on"))
    text = page(admin_client, address)
    assert line(text, 2)["subject"] == "subject-a (already here)"
    assert "1 record can be imported: 0 new subjects, 0 values" in text

    rows = [row(subject="subject-b"), row(subject="subject-b", time="1991-03-21T12:00")]
    text = page(admin_client, waiting(upload(admin_client, source, rows, add_to_existing="on")))
    assert line(text, 2)["subject"] == "subject-b (new)"
    assert line(text, 3)["subject"] == "subject-b (as on line 2)"
    assert "2 records can be imported: 1 new subject, 0 values" in text


@pytest.mark.django_db
def test_refused_rows_are_all_shown_by_line_and_nothing_is_kept(
    admin_client: Client, source: Source, variables: dict[str, Variable]
) -> None:
    rows = [
        row(subject="subject-a"),
        row(subject="subject-b", latitude="91"),
        row(subject="subject-c", profession="pilot"),
    ]
    response = upload(admin_client, source, rows)
    assert response.status_code == 422
    text = text_of(response)
    assert "Nothing was imported: 2 of 3 rows refused" in text
    assert "line 3: " in text and "line 4: 'pilot' is not one of profession's categories" in text
    assert "line 2: " not in text
    assert not PendingImport.objects.exists() and nothing_saved()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("file", "reason"),
    [
        (csv_file([row(subject="Zoë")], encoding="latin-1"), "the file isn't UTF-8"),
        (csv_file([row(colour="red")]), "unknown column 'colour'"),
        (csv_file([{"subject": "subject-a"}]), "missing column 'time'"),
        (csv_file([row(subject="subject\x00a")]), "NUL"),
    ],
)
def test_a_refused_file_shows_why_and_nothing_is_kept(
    admin_client: Client, source: Source, file: SimpleUploadedFile, reason: str
) -> None:
    response = upload(admin_client, source, file)
    assert response.status_code == 422
    assert reason in text_of(response)
    assert not PendingImport.objects.exists() and nothing_saved()


@pytest.mark.django_db
def test_the_form_s_own_errors_are_shown(admin_client: Client, source: Source) -> None:
    response = admin_client.post(reverse("csv-import"), {"source": source.pk})
    assert response.status_code == 400 and "This field is required" in text_of(response)
    gone = Source.objects.create(citation="A source since deleted").pk  # ids aren't reused
    Source.objects.filter(pk=gone).delete()
    response = admin_client.post(reverse("csv-import"), {"file": csv_file([row()]), "source": gone})
    assert response.status_code == 400 and "Select a valid choice" in text_of(response)
    assert not PendingImport.objects.exists() and nothing_saved()


@pytest.mark.django_db
def test_a_new_upload_replaces_the_waiting_one_and_a_refused_one_changes_nothing(
    admin_client: Client, source: Source
) -> None:
    first = waiting(upload(admin_client, source, [row()]))
    second = waiting(upload(admin_client, source, csv_file([row()], name="other.csv")))
    assert first != second and PendingImport.objects.count() == 1
    page(admin_client, first, status=404)

    assert upload(admin_client, source, [row(latitude="91")]).status_code == 422
    text = page(admin_client, reverse("csv-import"))
    assert f'href="{second}"' in text and "other.csv" in text  # still waiting
    page(admin_client, second)


# --- Importing ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_import_saves_every_row_and_opens_the_dataset_saying_what_was_imported(
    admin_client: Client, source: Source, variables: dict[str, Variable]
) -> None:
    dataset = Dataset.objects.create(name="demo")
    address = waiting(upload(admin_client, source, TWO_ROWS, dataset=dataset.pk))
    response = admin_client.post(save_address(address))
    assert response.status_code == 302
    assert response["Location"] == reverse("dataset", args=[dataset.pk])
    opened = page(admin_client, response["Location"])
    assert "Imported 2 records: 2 new subjects, 3 values" in opened

    labels = dataset.records.values_list("subject__label", flat=True)
    assert sorted(labels) == ["subject-a", "subject-b"]
    assert {record.source for record in Record.objects.all()} == {source}
    assert Observation.objects.filter(source=source).count() == 3
    assert not PendingImport.objects.exists()
    page(admin_client, address, status=404)


@pytest.mark.django_db
def test_without_a_dataset_import_opens_the_records_page(
    admin_client: Client, source: Source
) -> None:
    response = admin_client.post(save_address(waiting(upload(admin_client, source, [row()]))))
    assert response.status_code == 302 and response["Location"] == reverse("records")
    assert "Imported 1 record: 1 new subject, 0 values" in page(admin_client, reverse("records"))
    assert Record.objects.get().datasets.count() == 0


@pytest.mark.django_db
def test_a_waiting_file_is_checked_again_when_shown_and_when_imported(
    admin_client: Client, source: Source
) -> None:
    address = waiting(upload(admin_client, source, [row()]))
    Subject.objects.create(label="subject-a")  # made since the file was checked

    text = page(admin_client, address, status=422)
    assert "line 2: subject 'subject-a' already exists" in text
    assert f'action="{save_address(address)}"' not in text

    response = admin_client.post(save_address(address))
    assert response.status_code == 422
    assert "line 2: subject 'subject-a' already exists" in text_of(response)
    assert not Record.objects.exists() and Subject.objects.count() == 1


@pytest.mark.django_db
def test_a_waiting_file_whose_variable_is_gone_is_refused_as_a_whole(
    admin_client: Client, source: Source, variables: dict[str, Variable]
) -> None:
    address = waiting(upload(admin_client, source, [row(profession="athlete")]))
    variables["profession"].delete()  # since the file was checked
    text = page(admin_client, address, status=422)
    assert "This file can't be imported now: unknown column 'profession'" in text
    assert admin_client.post(save_address(address)).status_code == 422
    assert nothing_saved()


@pytest.mark.django_db
def test_import_needs_a_post(admin_client: Client, source: Source) -> None:
    address = waiting(upload(admin_client, source, [row()]))
    assert admin_client.get(save_address(address)).status_code == 405
    assert not Record.objects.exists()


# --- Accounts -------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_waiting_file_is_its_account_s_alone(
    admin_client: Client, client: Client, source: Source
) -> None:
    address = waiting(upload(admin_client, source, [row()]))
    client.force_login(User.objects.create_user("another"))
    assert client.get(address).status_code == 404
    assert client.post(save_address(address)).status_code == 404
    assert address not in page(client, reverse("csv-import"))
    assert nothing_saved()


@pytest.mark.django_db
def test_the_upload_pages_need_a_login(client: Client) -> None:
    for address in (
        reverse("csv-import"),
        reverse("csv-import-template"),
        reverse("pending-import", args=[1]),
    ):
        response = client.get(address)
        assert response.status_code == 302 and "login" in response["Location"], address
    response = client.post(reverse("pending-import-save", args=[1]))
    assert response.status_code == 302 and "login" in response["Location"]
