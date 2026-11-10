"""Getting around: a home page, a records page, a Back button in the app, and no dead ends.

- The home page (`/`) shows the platform's places, records, datasets, features and hypotheses,
  each with how many it holds, and the five latest runs and hypothesis results. With nothing
  yet, it says where to start. The app's window opens here.
- The records page lists every record, 100 at a time, by subject: its reading, zone and source,
  and links to its chart and the datasets holding it. A search finds records by subject.
- In the app, the header has a Back button, since a window has no browser's; elsewhere the
  browser has its own. The header links home and to the records.
- Every page links somewhere besides the header: a stored chart to the runs and records that
  stored it, a record's pages to the records page and the record's datasets. A crawl from the
  home page follows every link and finds no page that fails and none that's a dead end.

Markup the tests rely on: the home page's places carry data-place, with their count in a
class="count" element; runs and results rows carry data-run and data-result; records rows
data-record; the app's Back button data-back.
"""

import html
import re
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin, urlsplit

import pytest
from django.test import Client
from django.urls import Resolver404, resolve, reverse
from pytest_django.fixtures import Settings

from astro_research.chart import BirthRecord, ChartSettings
from astro_research.civil_time import UtcTime
from astro_research.ephemeris import Ephemeris
from astro_research.geo import GeoPosition
from astro_research.measurements import MAJOR
from research_platform.hypotheses import run_hypothesis
from research_platform.models import (
    AnalysisRun,
    Category,
    Dataset,
    Feature,
    Hypothesis,
    Observation,
    Record,
    Source,
    StoredChart,
    Subject,
    Variable,
)
from research_platform.runner import run_dataset

LONDON = GeoPosition(latitude=51.5074, longitude=-0.1278)
SETTINGS = ChartSettings(aspect_angles=MAJOR)
PLACES = ("records", "datasets", "features", "hypotheses")
# Not pages: what HTMX swaps into a page, and downloads.
FRAGMENTS = {
    "record-chart-pairs",
    "record-compare-pairs",
    "record-sensitivity-series",
    "record-sensitivity-detail",
    "record-parans-events",
    "record-parans-gaps",
    "record-parans-stars",
}
DOWNLOADS = {
    "record-chart-json",
    "record-sensitivity-csv",
    "record-parans-csv",
    "run-outcomes-csv",
    "run-charts-jsonl",
    "run-chart-values-csv",
    "run-points-csv",
    "run-pairs-csv",
    "result-inputs-csv",
}
# Every kind of page a crawl from home must reach. A comparison is opened from the admin's
# record list, and a hypothesis with results can't be edited, so neither is here.
PAGES = {
    "home",
    "records",
    "record-chart",
    "record-sensitivity",
    "record-eclipses",
    "record-parans",
    "datasets",
    "dataset",
    "run",
    "stored-chart",
    "features",
    "feature",
    "feature-new",
    "hypotheses",
    "hypothesis",
    "hypothesis-new",
}


def birth(hours: int) -> BirthRecord:
    return BirthRecord(
        time=UtcTime(utc=datetime(1990, 3, 21, tzinfo=UTC) + timedelta(hours=hours)),
        place=LONDON,
    )


@pytest.fixture
def study(store: Callable[..., Record], source: Source, eph: Ephemeris) -> AnalysisRun:
    """Four subjects, two athletes and two musicians, run, with a feature and a hypothesis
    that has a result."""
    profession = Variable.objects.create(name="profession", kind=Variable.Kind.CATEGORY)
    athlete = Category.objects.create(variable=profession, name="athlete")
    musician = Category.objects.create(variable=profession, name="musician")
    records = []
    for n in range(4):
        subject = Subject.objects.create(label=f"study-{n}")
        records.append(store(birth(5 * n), subject=subject))
        Observation.objects.create(
            variable=profession,
            subject=subject,
            source=source,
            category=athlete if n < 2 else musician,
        )
    dataset = Dataset.objects.create(name="study")
    dataset.records.add(*records)
    run = run_dataset(dataset, SETTINGS, eph)
    feature = Feature(
        name="Mars rising",
        kind="contact",
        settings={"point": "mars", "others": ["ascendant"], "angles": [0], "orb": 90},
    )
    feature.full_clean()
    feature.save()
    hypothesis = Hypothesis(
        name="Mars rising in athletes",
        statement="Athletes are born with Mars rising more often than musicians.",
        feature=feature,
        variable=profession,
        category=athlete,
        run=run,
        analysis="fisher-exact",
        settings={"alternative": "greater", "alpha": 0.05},
    )
    hypothesis.full_clean()
    hypothesis.save()
    return run_hypothesis(hypothesis)


def page(client: Client, address: str) -> str:
    response = client.get(address)
    assert response.status_code == 200, (address, response.status_code)
    return html.unescape(response.content.decode())


def outside_header(text: str) -> str:
    return re.sub(r"<header>.*?</header>", "", text, count=1, flags=re.S)


def ids(text: str, attribute: str) -> list[int]:
    return [int(value) for value in re.findall(rf'{attribute}="(\d+)"', text)]


def place(text: str, name: str) -> str:
    found = re.search(rf'<a[^>]*data-place="{name}"[^>]*>.*?</a>', text, re.S)
    assert found is not None, name
    return found.group(0)


def row(text: str, attribute: str, pk: int) -> str:
    found = re.search(rf'<tr[^>]*{attribute}="{pk}".*?</tr>', text, re.S)
    assert found is not None, f'{attribute}="{pk}"'
    return found.group(0)


# --- The home page --------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_home_page_shows_each_place_with_how_many_it_holds(
    admin_client: Client, study: AnalysisRun
) -> None:
    Dataset.objects.create(name="another")  # so no two places hold as many
    for point in ("sun", "moon"):
        feature = Feature(
            name=f"{point} rising",
            kind="contact",
            settings={"point": point, "others": ["ascendant"], "angles": [0], "orb": 10},
        )
        feature.full_clean()
        feature.save()
    counts = {"records": 4, "datasets": 2, "features": 3, "hypotheses": 1}
    text = page(admin_client, reverse("home"))
    for name, count in counts.items():
        link = place(text, name)
        assert f'href="{reverse(name)}"' in link, name
        assert re.search(rf'class="count">\s*{count}\s*<', link), name


@pytest.mark.django_db
def test_the_home_page_shows_the_five_latest_runs_and_results(
    admin_client: Client, study: AnalysisRun, eph: Ephemeris
) -> None:
    dataset = study.hypothesis.run.dataset
    runs = [study.hypothesis.run] + [run_dataset(dataset, SETTINGS, eph) for _ in range(5)]
    results = [study] + [run_hypothesis(study.hypothesis) for _ in range(5)]
    text = page(admin_client, reverse("home"))
    assert ids(text, "data-run") == [run.pk for run in reversed(runs[1:])]
    assert ids(text, "data-result") == [result.pk for result in reversed(results[1:])]
    newest = runs[-1]
    assert f'href="{reverse("run", args=[newest.pk])}"' in row(text, "data-run", newest.pk)
    latest = row(text, "data-result", results[-1].pk)
    assert f'href="{reverse("hypothesis", args=[study.hypothesis.pk])}"' in latest
    assert "Mars rising in athletes" in latest


@pytest.mark.django_db
def test_an_empty_platform_s_home_page_says_where_to_start(admin_client: Client) -> None:
    text = page(admin_client, reverse("home"))
    assert "Nothing here yet" in text
    assert f'href="{reverse("admin:research_platform_record_add")}"' in text
    for name in PLACES:
        assert re.search(r'class="count">\s*0\s*<', place(text, name)), name
    assert not ids(text, "data-run") and not ids(text, "data-result")


@pytest.mark.django_db
def test_the_header_links_home_and_to_the_records(admin_client: Client) -> None:
    header = re.search(r"<header>.*?</header>", page(admin_client, reverse("datasets")), re.S)
    assert header is not None
    assert f'href="{reverse("home")}"' in header.group(0)
    assert f'href="{reverse("records")}"' in header.group(0)


# --- The records page -----------------------------------------------------------------------


@pytest.mark.django_db
def test_the_records_page_lists_each_record_by_subject_ignoring_case(
    admin_client: Client, study: AnalysisRun, store: Callable[..., Record]
) -> None:
    # Made last, so their ids don't follow their labels' order.
    # "Zola" sorts before "study-0" when case counts, and after it when it doesn't.
    for label in ("Aaron", "Zola"):
        store(birth(99), subject=Subject.objects.create(label=label))
    text = page(admin_client, reverse("records"))
    records = sorted(
        Record.objects.select_related("subject", "source"),
        key=lambda record: (record.subject.label.casefold(), record.pk),
    )
    labels = [record.subject.label for record in records]
    assert labels[:2] == ["Aaron", "study-0"] and labels[-1] == "Zola"
    assert ids(text, "data-record") == [record.pk for record in records]
    assert "6 records" in text
    dataset = study.hypothesis.run.dataset
    for record in records:
        cells = row(text, "data-record", record.pk)
        assert record.subject.label in cells and record.reading in cells
        assert record.source.citation in cells
        assert f'href="{reverse("record-chart", args=[record.pk])}"' in cells
        in_study = record.subject.label.startswith("study-")
        assert (f'href="{reverse("dataset", args=[dataset.pk])}"' in cells) is in_study


@pytest.mark.django_db
def test_the_records_page_finds_records_by_subject(
    admin_client: Client, study: AnalysisRun
) -> None:
    one = Record.objects.get(subject__label="study-1")
    assert ids(page(admin_client, reverse("records") + "?q=study-1"), "data-record") == [one.pk]
    assert len(ids(page(admin_client, reverse("records") + "?q=STUDY"), "data-record")) == 4
    # Accented letters too, which SQLite's own matching treats as different (observed).
    emile = Record.objects.create(
        **{
            field.name: getattr(one, field.name)
            for field in Record._meta.concrete_fields
            if field.name not in {"id", "subject"}
        },
        subject=Subject.objects.create(label="Émile Zola"),
    )
    for query in ("émile", "ÉMILE", "zola"):
        found = ids(page(admin_client, reverse("records") + f"?q={query}"), "data-record")
        assert found == [emile.pk], query
    nobody = page(admin_client, reverse("records") + "?q=nobody")
    assert not ids(nobody, "data-record") and "No records match" in nobody
    assert re.search(r'name="q"[^>]*value="nobody"', nobody)


@pytest.mark.django_db
def test_the_records_page_shows_100_at_a_time(
    admin_client: Client, subject: Subject, source: Source
) -> None:
    Record.objects.bulk_create(
        Record.from_birth_record(birth(n), subject=subject, source=source) for n in range(101)
    )
    first = page(admin_client, reverse("records"))
    assert len(ids(first, "data-record")) == 100 and "101 records" in first
    assert 'href="?page=2"' in first or f'href="{reverse("records")}?page=2"' in first
    second = page(admin_client, reverse("records") + "?page=2")
    assert len(ids(second, "data-record")) == 1
    assert set(ids(first, "data-record")).isdisjoint(ids(second, "data-record"))
    searched = page(admin_client, reverse("records") + "?q=subject-1")
    assert "q=subject-1&page=2" in searched  # the search is kept from page to page


@pytest.mark.django_db
def test_the_new_pages_need_a_login(client: Client) -> None:
    for name in ("home", "records"):
        response = client.get(reverse(name))
        assert response.status_code == 302 and "login" in response["Location"], name


# --- The app --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_in_the_app_the_header_has_a_back_button(admin_client: Client, settings: Settings) -> None:
    settings.RESEARCH_DESKTOP = True
    assert "data-back" in page(admin_client, reverse("datasets"))
    settings.RESEARCH_DESKTOP = False
    assert "data-back" not in page(admin_client, reverse("datasets"))


# --- No dead ends ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_stored_chart_links_to_the_runs_and_records_that_stored_it(
    admin_client: Client, study: AnalysisRun
) -> None:
    run = study.hypothesis.run
    outcome = run.outcomes.exclude(chart=None).select_related("chart").first()
    assert outcome is not None and outcome.chart is not None
    text = outside_header(page(admin_client, reverse("stored-chart", args=[outcome.chart.pk])))
    assert f'href="{reverse("run", args=[run.pk])}"' in text
    assert f'href="{reverse("record-chart", args=[outcome.record.pk])}"' in text


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name", ["record-chart", "record-sensitivity", "record-eclipses", "record-parans"]
)
def test_a_record_s_pages_link_to_the_records_and_its_datasets(
    admin_client: Client, study: AnalysisRun, name: str
) -> None:
    record = Record.objects.get(subject__label="study-0")
    text = outside_header(page(admin_client, reverse(name, args=[record.pk])))
    assert f'href="{reverse("records")}"' in text
    dataset = study.hypothesis.run.dataset
    assert f'href="{reverse("dataset", args=[dataset.pk])}"' in text


def links(text: str, address: str) -> list[str]:
    """The page's links to the platform, as addresses."""
    found = []
    for href in re.findall(r'href="([^"]*)"', text):
        target = urljoin("http://testserver" + address, href)
        parts = urlsplit(target)
        if parts.netloc == "testserver":
            found.append(parts.path + (f"?{parts.query}" if parts.query else ""))
    return found


def name_of(address: str) -> str | None:
    try:
        return resolve(urlsplit(address).path).url_name
    except Resolver404:
        return None


def skipped(address: str) -> bool:
    path = urlsplit(address).path
    if path.startswith(("/admin/", "/login/", "/logout/", "/static/", "/desktop/")):
        return True
    return name_of(address) in FRAGMENTS | DOWNLOADS


@pytest.mark.django_db
def test_every_link_leads_somewhere_and_no_page_is_a_dead_end(
    admin_client: Client, study: AnalysisRun
) -> None:
    # Make the stored chart and the run's tables part of the crawl's world.
    assert StoredChart.objects.exists()
    queue = deque([(reverse("home"), "(start)")])
    seen = {reverse("home")}
    reached: set[str] = set()
    failures, dead_ends = [], []
    while queue:
        address, linked_from = queue.popleft()
        assert len(seen) <= 600, "the crawl isn't ending: links keep growing"
        response = admin_client.get(address)
        if response.status_code != 200:
            failures.append(f"{address} ({response.status_code}), linked from {linked_from}")
            continue
        reached.add(name_of(address) or address)
        text = html.unescape(response.content.decode())
        own = [link for link in links(outside_header(text), address) if link != address]
        if not [link for link in own if not link.startswith("/static/")]:
            dead_ends.append(address)
        for link in links(text, address):
            if link not in seen and not skipped(link):
                seen.add(link)
                queue.append((link, address))
    assert not failures, failures
    assert not dead_ends, dead_ends
    assert reached >= PAGES, sorted(PAGES - reached)
