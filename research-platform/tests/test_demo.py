"""The Gauquelin demo: the Open Gauquelin Database's group A1, loaded into an empty platform.

`demo.load_gauquelin` downloads the group's zip with the fetch it's given, checks its SHA-256,
converts its one file, A1.csv, with seed 1, and in one transaction makes the category variable
`group` (champion, control), a source for the champions and one for the controls, and the
dataset "Gauquelin A1", importing both files through the CSV import's checks. It's refused once
there are records. Anything refused raises DemoRefused and changes nothing.

`demo.fetch_https` downloads with the Mac's own trusted certificates, through truststore, and
takes at most `demo.LARGEST` bytes. It calls `urllib.request.urlopen(url, timeout=...,
context=...)`, which the tests here replace.

In the desktop app, an empty home page offers the demo as a form, a POST with Django's CSRF
token and data-once. Pressing it opens the dataset's page with what to do next; a refusal shows
on the home page. The view looks up `demo.A1` and `demo.fetch_https` when pressed, so the tests
replace them. Outside the app its address isn't found.

No test here touches the network: each zip is made here, of made-up people.
"""

import csv
import hashlib
import html
import io
import re
import ssl
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from pytest_django.fixtures import Settings

from astro_research.chart import BirthRecord
from astro_research.civil_time import UtcTime
from astro_research.geo import GeoPosition
from research_platform import demo
from research_platform.models import (
    Category,
    Dataset,
    Observation,
    Record,
    Source,
    Subject,
    Variable,
)

HEADER = [
    "GQID",
    "MUID",
    "NUM",
    "FNAME",
    "GNAME",
    "DATE",
    "TZO",
    "DATE-UT",
    "PLACE",
    "C2",
    "C3",
    "CY",
    "LG",
    "LAT",
    "GEOID",
    "OCCU",
]
RECORD = BirthRecord(
    time=UtcTime(utc=datetime(2000, 1, 1, 12, tzinfo=UTC)),
    place=GeoPosition(latitude=51.5, longitude=0),
)


def person(n: int, **cells: str) -> dict[str, str]:
    """Made-up person n, born at n o'clock and 15 minutes on 1930-01-n, 1 hour ahead of UT."""
    local = datetime(1930, 1, n, n % 24, 15)
    row = {
        "GQID": f"A1-{n}",
        "NUM": str(n),
        "FNAME": "Synthetic",
        "GNAME": f"Person {n}",
        "DATE": local.strftime("%Y-%m-%d %H:%M"),
        "TZO": "+01:00",
        "DATE-UT": (local - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M"),
        "PLACE": "Testville",
        "CY": "FR",
        "LG": f"{n / 4:g}",
        "LAT": f"{44 + n / 10:g}",
        "OCCU": "cyclist",
    }
    return row | cells


ROWS = [person(1), person(2), person(3), person(4), person(5, DATE="1930-01-05")]  # 5: no time


def a1_text(rows: list[dict[str, str]]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=HEADER, restval="", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def zipped(files: dict[str, str]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return out.getvalue()


CONTENT = zipped({"A1.csv": a1_text(ROWS)})


def made_for(content: bytes) -> demo.DemoFile:
    """A1 as it would be if this version had been made for `content`."""
    return replace(demo.A1, sha256=hashlib.sha256(content).hexdigest())


class Fetch:
    """A download that gives `content` and keeps the addresses it was asked for."""

    def __init__(self, content: bytes) -> None:
        self.content = content
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        return self.content


def counts() -> tuple[int, ...]:
    """How many of everything the demo makes there are."""
    made = (Variable, Category, Source, Dataset, Subject, Record, Observation)
    return tuple(model.objects.count() for model in made)


def page(client: Client, address: str) -> str:
    response = client.get(address)
    assert response.status_code == 200, response.content.decode()[:500]
    return html.unescape(response.content.decode())


# --- Loading --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_loading_makes_the_variable_the_sources_and_the_dataset() -> None:
    fetch = Fetch(CONTENT)
    loaded = demo.load_gauquelin(fetch, made_for(CONTENT))
    assert fetch.urls == [demo.A1.url]
    assert (loaded.champions, loaded.controls, loaded.left_out) == (4, 4, 1)
    assert loaded.dataset.name == "Gauquelin A1"
    group = Variable.objects.get(name="group")
    assert group.kind == Variable.Kind.CATEGORY
    assert sorted(group.categories.values_list("name", flat=True)) == ["champion", "control"]
    assert Record.objects.filter(datasets=loaded.dataset).count() == 8 == Record.objects.count()
    groups = dict(
        Observation.objects.filter(variable=group).values_list("subject__label", "category__name")
    )
    assert groups == {f"A1-{n}": "champion" for n in range(1, 5)} | {
        f"control-{n:04d}": "control" for n in range(1, 5)
    }
    assert not Subject.objects.filter(label="A1-5").exists()  # left out: no birth time


@pytest.mark.django_db
def test_the_sources_say_where_each_file_came_from() -> None:
    demo.load_gauquelin(Fetch(CONTENT), made_for(CONTENT))
    champions, controls = Source.objects.order_by("pk")
    assert "Open Gauquelin Database" in champions.citation and "A1" in champions.citation
    assert "2024-01-11" in champions.citation  # the file's own date
    assert "https://opengauquelin.org/about" in champions.notes  # where its license is
    assert champions.retrieved_on == timezone.localdate()
    assert "open_gauquelin" in controls.citation and "seed 1" in controls.citation
    for prefix, source in (("A1-", champions), ("control-", controls)):
        records = Record.objects.filter(subject__label__startswith=prefix)
        assert {record.source for record in records} == {source}
        values = Observation.objects.filter(subject__label__startswith=prefix)
        assert {value.source for value in values} == {source}


@pytest.mark.django_db
def test_a_file_that_has_changed_is_refused() -> None:
    before = counts()
    with pytest.raises(demo.DemoRefused, match="has changed since this version of the app"):
        demo.load_gauquelin(Fetch(CONTENT))  # checked against A1's own SHA-256
    assert counts() == before


@pytest.mark.django_db
@pytest.mark.parametrize(
    "content, why",
    [
        (zipped({"A1.csv": a1_text(ROWS), "B1.csv": a1_text(ROWS)}), "A1.csv"),
        (zipped({"B1.csv": a1_text(ROWS)}), "A1.csv"),
        (b"not a zip", "zip"),
    ],
    ids=["two files", "another file", "not a zip"],
)
def test_a_download_that_isn_t_a_zip_of_a1_csv_alone_is_refused(content: bytes, why: str) -> None:
    before = counts()
    with pytest.raises(demo.DemoRefused, match=why):
        demo.load_gauquelin(Fetch(content), made_for(content))
    assert counts() == before


@pytest.mark.django_db
def test_a_download_that_fails_is_refused() -> None:
    def fetch(url: str) -> bytes:
        raise demo.DemoRefused("couldn't download it: no internet")

    before = counts()
    with pytest.raises(demo.DemoRefused, match="no internet"):
        demo.load_gauquelin(fetch, made_for(CONTENT))
    assert counts() == before


@pytest.mark.django_db
def test_it_s_refused_once_there_are_records(store: Callable[..., Record]) -> None:
    store(RECORD)
    before, fetch = counts(), Fetch(CONTENT)
    with pytest.raises(demo.DemoRefused, match="already has records"):
        demo.load_gauquelin(fetch, made_for(CONTENT))
    assert counts() == before
    assert fetch.urls == []  # refused before downloading anything


@pytest.mark.django_db
def test_it_s_refused_if_its_dataset_s_name_is_taken() -> None:
    Dataset.objects.create(name="Gauquelin A1")
    before, fetch = counts(), Fetch(CONTENT)
    with pytest.raises(demo.DemoRefused, match="a dataset named Gauquelin A1 already exists"):
        demo.load_gauquelin(fetch, made_for(CONTENT))
    assert counts() == before
    assert fetch.urls == []


@pytest.mark.django_db
def test_a_group_variable_with_both_categories_is_used() -> None:
    group = Variable.objects.create(name="group", kind=Variable.Kind.CATEGORY)
    for name in ("champion", "control"):
        Category.objects.create(variable=group, name=name)
    demo.load_gauquelin(Fetch(CONTENT), made_for(CONTENT))
    assert list(Variable.objects.all()) == [group]
    assert group.categories.count() == 2
    assert Observation.objects.filter(variable=group).count() == 8


@pytest.mark.django_db
@pytest.mark.parametrize(
    "kind, categories",
    [(Variable.Kind.NUMBER, []), (Variable.Kind.CATEGORY, ["champion"])],
    ids=["a number", "without control"],
)
def test_a_group_variable_that_isn_t_champion_and_control_is_refused(
    kind: Variable.Kind, categories: list[str]
) -> None:
    group = Variable.objects.create(name="group", kind=kind)
    for name in categories:
        Category.objects.create(variable=group, name=name)
    before, fetch = counts(), Fetch(CONTENT)
    with pytest.raises(
        demo.DemoRefused,
        match="group already exists, and isn't a category with champion and control",
    ):
        demo.load_gauquelin(fetch, made_for(CONTENT))
    assert counts() == before
    assert fetch.urls == []


@pytest.mark.django_db
def test_a_row_the_import_refuses_changes_nothing() -> None:
    Subject.objects.create(label="A1-2")  # a subject without records: the demo is still offered
    before = counts()
    with pytest.raises(demo.DemoRefused, match="A1-2"):
        demo.load_gauquelin(Fetch(CONTENT), made_for(CONTENT))
    assert counts() == before


# --- Downloading ----------------------------------------------------------------------------


class Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *exception: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]


@pytest.mark.parametrize("size", [demo.LARGEST, demo.LARGEST + 1])
def test_a_download_is_taken_up_to_its_limit_with_the_mac_s_certificates(
    monkeypatch: pytest.MonkeyPatch, size: int
) -> None:
    calls: list[tuple[str, Any, Any]] = []

    def urlopen(url: str, *, timeout: float, context: ssl.SSLContext) -> Response:
        calls.append((url, timeout, context))
        return Response(b"x" * size)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    address = "https://example.test/A1.csv.zip"
    if size > demo.LARGEST:
        with pytest.raises(demo.DemoRefused, match="more than 1 MiB"):
            demo.fetch_https(address)
    else:
        assert demo.fetch_https(address) == b"x" * size
    ((url, timeout, context),) = calls
    assert (url, timeout) == (address, demo.TIMEOUT)
    assert type(context).__module__.startswith("truststore")
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


@pytest.mark.parametrize(
    "error, why",
    [
        (urllib.error.URLError("no route to host"), "no route to host"),
        (TimeoutError("timed out"), "timed out"),
    ],
    ids=["no route", "timeout"],
)
def test_a_download_that_fails_says_why(
    monkeypatch: pytest.MonkeyPatch, error: Exception, why: str
) -> None:
    def urlopen(url: str, **options: object) -> Response:
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(demo.DemoRefused, match=f"couldn't download .*{why}"):
        demo.fetch_https("https://example.test/A1.csv.zip")


# --- The button -----------------------------------------------------------------------------


def demo_form(text: str) -> tuple[str, str] | None:
    """The demo form's opening tag and what's inside it, if the page has it."""
    action = reverse("demo-gauquelin")
    found = re.search(rf'(<form[^>]*action="{action}"[^>]*>)(.*?)</form>', text, re.S)
    return None if found is None else (found.group(1), found.group(2))


@pytest.mark.django_db
def test_in_the_app_an_empty_home_page_offers_the_demo(
    admin_client: Client, settings: Settings
) -> None:
    settings.RESEARCH_DESKTOP = True
    text = page(admin_client, reverse("home"))
    form = demo_form(text)
    assert form is not None
    tag, inside = form
    assert 'method="post"' in tag and "data-once" in tag
    assert "csrfmiddlewaretoken" in inside and "Load the Gauquelin demo" in inside
    assert "opengauquelin.org" in text


@pytest.mark.django_db
def test_the_demo_isn_t_offered_on_the_web_or_once_there_are_records(
    admin_client: Client, settings: Settings, store: Callable[..., Record]
) -> None:
    settings.RESEARCH_DESKTOP = False
    assert demo_form(page(admin_client, reverse("home"))) is None
    settings.RESEARCH_DESKTOP = True
    store(RECORD)
    assert demo_form(page(admin_client, reverse("home"))) is None


@pytest.mark.django_db
def test_pressing_it_in_the_app_opens_the_dataset_with_what_to_do_next(
    admin_client: Client, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.RESEARCH_DESKTOP = True
    monkeypatch.setattr(demo, "fetch_https", Fetch(CONTENT))
    monkeypatch.setattr(demo, "A1", made_for(CONTENT))
    response = admin_client.post(reverse("demo-gauquelin"))
    assert response.status_code == 302, response.status_code
    dataset = Dataset.objects.get()
    assert response["Location"] == reverse("dataset", args=[dataset.pk])
    messages = re.search(
        r'<ul class="messages">(.*?)</ul>', page(admin_client, response["Location"]), re.S
    )
    assert messages is not None
    said = messages.group(1)
    assert "4 champions and 4 controls" in said and "Run" in said and "Mars" in said


@pytest.mark.django_db
def test_a_refusal_shows_on_the_home_page_and_changes_nothing(
    admin_client: Client, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.RESEARCH_DESKTOP = True
    monkeypatch.setattr(demo, "fetch_https", Fetch(CONTENT))  # not what A1 was made for
    before = counts()
    response = admin_client.post(reverse("demo-gauquelin"))
    assert response.status_code == 400, response.status_code
    text = html.unescape(response.content.decode())
    assert 'data-error="request"' in text
    assert "has changed since this version of the app" in text
    assert demo_form(text) is not None  # still offered
    assert counts() == before


@pytest.mark.django_db
def test_outside_the_app_the_demo_s_address_isn_t_found(
    admin_client: Client, settings: Settings
) -> None:
    settings.RESEARCH_DESKTOP = False
    assert admin_client.post(reverse("demo-gauquelin")).status_code == 404


@pytest.mark.django_db
def test_the_demo_is_loaded_only_by_a_post_with_a_login(
    client: Client, admin_client: Client, settings: Settings
) -> None:
    settings.RESEARCH_DESKTOP = True
    assert admin_client.get(reverse("demo-gauquelin")).status_code == 405
    response = client.post(reverse("demo-gauquelin"))
    assert response.status_code == 302 and "login" in response["Location"]
