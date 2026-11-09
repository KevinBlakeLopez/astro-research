"""Datasets, runs and stored charts in the web interface.

A dataset's page lists its records and runs, and starts a run (a POST, with Django's CSRF check)
for up to 500 records; larger datasets are for `compute_dataset`. A run's page shows what it ran
with and each record's outcome, and downloads its outcomes as CSV and its stored charts as JSON
Lines, exactly as stored. A stored chart's page reads the document back and shows it; it has no
settings, since a stored chart's are fixed. Runs are never changed or deleted here.

Markup the tests rely on: rows carry data-dataset, data-record and data-run with the object's
id; outcome rows data-outcome="<record id>" with data-kind="chart" or data-kind="refusal".
"""

import csv
import hashlib
import html
import io
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from astro_research.chart import ENGINE_VERSION, BirthRecord, ChartSettings
from astro_research.civil_time import UtcTime, ZonedLocalTime
from astro_research.ephemeris import Ephemeris, EphemerisNotLoaded
from astro_research.geo import GeoPosition
from astro_research.measurements import MAJOR
from astro_research.result_format import chart_from_json
from research_platform import runner, runs_views
from research_platform.models import (
    CalculationRun,
    Dataset,
    Record,
    Source,
    StoredChart,
    Subject,
)
from research_platform.runner import run_dataset

LONDON = GeoPosition(latitude=51.5074, longitude=-0.1278)
FIRST = BirthRecord(
    time=ZonedLocalTime(local=datetime(1987, 6, 15, 4, 25), zone="Europe/London"), place=LONDON
)
SECOND = BirthRecord(
    time=UtcTime(utc=datetime(2024, 3, 20, 12, tzinfo=UTC)),
    place=GeoPosition(latitude=-33.8688, longitude=151.2093),
)
AMBIGUOUS = BirthRecord(  # 01:30 happens twice as British Summer Time ends
    time=ZonedLocalTime(local=datetime(1987, 10, 25, 1, 30), zone="Europe/London"), place=LONDON
)
SETTINGS = ChartSettings(aspect_angles=MAJOR)
FORM = {"aspects": "major", "time_scale": "ut1-from-iers"}


@pytest.fixture
def dataset(store: Callable[..., Record]) -> Dataset:
    """Three records of three subjects, the last one refused by the engine."""
    records = [
        store(FIRST),
        store(SECOND, subject=Subject.objects.create(label="subject-2")),
        store(AMBIGUOUS, subject=Subject.objects.create(label="subject-3")),
    ]
    dataset = Dataset.objects.create(name="sample", description="A synthetic sample")
    dataset.records.add(*records)
    return dataset


@pytest.fixture
def run(dataset: Dataset, eph: Ephemeris) -> CalculationRun:
    """Two charts computed, one refusal (observed)."""
    return run_dataset(dataset, SETTINGS, eph)


def large(subject: Subject, source: Source, size: int) -> Dataset:
    """`size` copies of one record, which a run computes once and reuses."""
    records = Record.objects.bulk_create(
        Record.from_birth_record(FIRST, subject=subject, source=source) for _ in range(size)
    )
    dataset = Dataset.objects.create(name=f"large-{size}")
    dataset.records.add(*records)
    return dataset


def page(client: Client, address: str) -> str:
    response = client.get(address)
    assert response.status_code == 200, response.content.decode()[:500]
    return html.unescape(response.content.decode())


def ids(text: str, attribute: str) -> list[int]:
    return [int(value) for value in re.findall(rf'{attribute}="(\d+)"', text)]


def aspects(cells: str) -> str:
    found = re.search(r'class="aspects">([^<]*)<', cells)
    assert found is not None
    return found.group(1).strip()


def body(response: Any) -> str:
    """A response's text, whether streamed or not."""
    if response.streaming:
        return b"".join(response.streaming_content).decode()
    return str(response.content.decode())


def row(text: str, attribute: str, pk: int) -> str:
    """The opening tag and cells of the row carrying attribute="pk"."""
    found = re.search(rf'<tr[^>]*{attribute}="{pk}".*?</tr>', text, re.S)
    assert found is not None, f'{attribute}="{pk}"'
    return found.group(0)


# --- Logging in and getting there -----------------------------------------------------------


@pytest.mark.django_db
def test_every_page_needs_a_login(client: Client, run: CalculationRun) -> None:
    stored = StoredChart.objects.first()
    assert stored is not None
    for address in (
        reverse("datasets"),
        reverse("dataset", args=[run.dataset.pk]),
        reverse("run", args=[run.pk]),
        reverse("run-outcomes-csv", args=[run.pk]),
        reverse("run-charts-jsonl", args=[run.pk]),
        reverse("stored-chart", args=[stored.pk]),
    ):
        response = client.get(address)
        assert response.status_code == 302 and "login" in response["Location"], address
    response = client.post(reverse("dataset-run", args=[run.dataset.pk]), FORM)
    assert response.status_code == 302 and "login" in response["Location"]
    assert CalculationRun.objects.count() == 1


@pytest.mark.django_db
def test_every_page_links_to_the_datasets(admin_client: Client, run: CalculationRun) -> None:
    record = run.dataset.records.order_by("pk").first()
    assert record is not None
    for address in (
        reverse("record-chart", args=[record.pk]),
        reverse("datasets"),
        reverse("run", args=[run.pk]),
    ):
        assert f'href="{reverse("datasets")}"' in page(admin_client, address), address


@pytest.mark.django_db
def test_a_dataset_s_admin_page_links_to_its_page(admin_client: Client, dataset: Dataset) -> None:
    text = page(admin_client, f"/admin/research_platform/dataset/{dataset.pk}/change/")
    assert f'href="{reverse("dataset", args=[dataset.pk])}"' in text
    assert "Save the dataset to run it." in page(
        admin_client, "/admin/research_platform/dataset/add/"
    )


# --- Datasets -------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_datasets_are_listed_by_name_with_their_size_and_latest_run(
    admin_client: Client, dataset: Dataset, eph: Ephemeris
) -> None:
    run_dataset(dataset, ChartSettings(aspect_angles=(90.0,)), eph)
    latest = run_dataset(dataset, SETTINGS, eph)
    empty = Dataset.objects.create(name="empty")
    text = page(admin_client, reverse("datasets"))
    assert ids(text, "data-dataset") == [empty.pk, dataset.pk]
    sample = row(text, "data-dataset", dataset.pk)
    assert "sample" in sample and re.search(r'class="size">\s*3\s*<', sample)
    assert f'href="{reverse("dataset", args=[dataset.pk])}"' in sample
    assert f'href="{reverse("run", args=[latest.pk])}"' in sample and "finished" in sample
    assert "/runs/" not in row(text, "data-dataset", empty.pk)


@pytest.mark.django_db
def test_a_dataset_shows_its_records(admin_client: Client, dataset: Dataset) -> None:
    text = page(admin_client, reverse("dataset", args=[dataset.pk]))
    assert "A synthetic sample" in text
    records = list(dataset.records.order_by("pk"))
    assert ids(text, "data-record") == [record.pk for record in records]
    for record in records:
        cells = row(text, "data-record", record.pk)
        assert record.subject.label in cells and record.reading in cells
        assert record.source.citation in cells
        assert f'href="{reverse("record-chart", args=[record.pk])}"' in cells
    assert "Europe/London" in row(text, "data-record", records[0].pk)


@pytest.mark.django_db
def test_a_dataset_shows_its_runs_newest_first(
    admin_client: Client, dataset: Dataset, eph: Ephemeris
) -> None:
    first = run_dataset(dataset, SETTINGS, eph)
    second = run_dataset(dataset, ChartSettings(aspect_angles=(90.0,)), eph)
    text = page(admin_client, reverse("dataset", args=[dataset.pk]))
    assert ids(text, "data-run") == [second.pk, first.pk]
    assert aspects(row(text, "data-run", first.pk)) == "0, 60, 90, 120, 180"
    assert aspects(row(text, "data-run", second.pk)) == "90"
    for each in (first, second):
        cells = row(text, "data-run", each.pk)
        assert "finished" in cells and f'href="{reverse("run", args=[each.pk])}"' in cells


# --- Starting a run -------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_run_started_from_the_dataset_s_page_opens_when_it_s_done(
    admin_client: Client, dataset: Dataset
) -> None:
    response = admin_client.post(reverse("dataset-run", args=[dataset.pk]), FORM)
    run = CalculationRun.objects.get()
    assert response.status_code == 302 and response["Location"] == reverse("run", args=[run.pk])
    assert run.dataset == dataset and run.status == CalculationRun.Status.FINISHED
    assert run.chart_settings() == SETTINGS
    assert (run.computed, run.reused, run.refused) == (2, 0, 1)


@pytest.mark.django_db
def test_the_run_form_is_a_post_with_a_csrf_token(admin_client: Client, dataset: Dataset) -> None:
    text = page(admin_client, reverse("dataset", args=[dataset.pk]))
    action = reverse("dataset-run", args=[dataset.pk])
    form = re.search(rf'<form[^>]*action="{action}"[^>]*>(.*?)</form>', text, re.S)
    assert form is not None
    assert 'method="post"' in form.group(0)
    assert 'name="csrfmiddlewaretoken"' in form.group(1)
    assert 'name="aspects"' in form.group(1) and 'name="time_scale"' in form.group(1)
    assert admin_client.get(reverse("dataset-run", args=[dataset.pk])).status_code == 405


@pytest.mark.django_db
def test_a_run_without_its_csrf_token_is_refused(django_user_model: Any, dataset: Dataset) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(django_user_model.objects.create_superuser("checker", "", "unused"))
    response = client.post(reverse("dataset-run", args=[dataset.pk]), FORM)
    assert response.status_code == 403
    assert not CalculationRun.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("form", "message"),
    [
        ({**FORM, "aspects": "banana"}, "aspects: 'banana' is neither an angle nor a set name"),
        ({**FORM, "time_scale": "sideways"}, "time scale 'sideways' is not one of"),
    ],
)
def test_settings_the_engine_can_t_use_are_refused_on_the_dataset_s_page(
    admin_client: Client, dataset: Dataset, form: dict[str, str], message: str
) -> None:
    response = admin_client.post(reverse("dataset-run", args=[dataset.pk]), form)
    assert response.status_code == 400
    text = html.unescape(response.content.decode())
    assert 'data-error="request"' in text and message in text
    assert re.search(rf'name="aspects"[^>]*value="{re.escape(form["aspects"])}"', text)
    assert not CalculationRun.objects.exists()


@pytest.mark.django_db
def test_a_dataset_of_500_records_runs_from_its_page(
    admin_client: Client, subject: Subject, source: Source
) -> None:
    dataset = large(subject, source, 500)  # 0.68 s (measured)
    response = admin_client.post(reverse("dataset-run", args=[dataset.pk]), FORM)
    run = CalculationRun.objects.get()
    assert response.status_code == 302 and response["Location"] == reverse("run", args=[run.pk])
    assert (run.computed, run.reused, run.refused) == (1, 499, 0)


@pytest.mark.django_db
def test_a_dataset_over_500_records_is_left_to_the_command(
    admin_client: Client, subject: Subject, source: Source
) -> None:
    dataset = large(subject, source, 501)
    response = admin_client.post(reverse("dataset-run", args=[dataset.pk]), FORM)
    assert response.status_code == 400
    text = html.unescape(response.content.decode())
    assert 'data-error="request"' in text
    assert "501 records" in text and "500" in text and "compute_dataset" in text
    assert not CalculationRun.objects.exists()


@pytest.mark.django_db
def test_a_run_that_fails_opens_showing_its_error(
    admin_client: Client,
    dataset: Dataset,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def failing(*args: Any, **kwargs: Any) -> Any:
        raise EphemerisNotLoaded("the files moved")

    monkeypatch.setattr(runner, "compute_chart", failing)
    response = admin_client.post(reverse("dataset-run", args=[dataset.pk]), FORM)
    run = CalculationRun.objects.get()
    assert response.status_code == 302 and response["Location"] == reverse("run", args=[run.pk])
    assert run.status == CalculationRun.Status.FAILED
    text = page(admin_client, reverse("run", args=[run.pk]))
    assert "failed" in text and "EphemerisNotLoaded" in text and "the files moved" in text
    logged = [entry for entry in caplog.records if entry.name == "research_platform.runs_views"]
    assert [entry.getMessage() for entry in logged] == [
        f"run {run.pk} of dataset {dataset.pk} failed"
    ]
    assert logged[0].exc_info is not None  # the traceback is in the server's log


# --- A run ----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_run_shows_what_it_ran_with(admin_client: Client, run: CalculationRun) -> None:
    text = page(admin_client, reverse("run", args=[run.pk]))
    provenance = run.ephemeris_provenance()
    assert "finished" in text and ENGINE_VERSION in text
    assert "ut1-from-iers" in text and "0, 60, 90, 120, 180" in text
    assert f"DE{provenance.de_number}" in text and provenance.binding in text
    for _name, sha256 in provenance.file_sha256:
        assert sha256 in text
    assert provenance.ut1_data_sha256 in text
    assert "2 computed" in text and "0 reused" in text and "1 refused" in text
    assert f'href="{reverse("dataset", args=[run.dataset.pk])}"' in text


@pytest.mark.django_db
def test_a_run_shows_each_record_s_outcome(admin_client: Client, run: CalculationRun) -> None:
    text = page(admin_client, reverse("run", args=[run.pk]))
    outcomes = list(run.outcomes.order_by("record__pk"))
    assert ids(text, "data-outcome") == [outcome.record.pk for outcome in outcomes]
    for outcome in outcomes:
        cells = row(text, "data-outcome", outcome.record.pk)
        assert outcome.record.subject.label in cells
        if outcome.chart is None:
            assert 'data-kind="refusal"' in cells
            assert outcome.refusal_class in cells and outcome.refusal_message in cells
        else:
            assert 'data-kind="chart"' in cells
            assert f'href="{reverse("stored-chart", args=[outcome.chart.pk])}"' in cells


@pytest.mark.django_db
@pytest.mark.parametrize(("show", "kind"), [("charts", "chart"), ("refusals", "refusal")])
def test_a_run_s_outcomes_can_be_narrowed(
    admin_client: Client, run: CalculationRun, show: str, kind: str
) -> None:
    text = page(admin_client, reverse("run", args=[run.pk]) + f"?show={show}")
    kinds = re.findall(r'data-kind="([a-z]+)"', text)
    assert kinds and set(kinds) == {kind}


@pytest.mark.django_db
def test_a_narrowing_the_page_doesn_t_know_is_refused(
    admin_client: Client, run: CalculationRun
) -> None:
    response = admin_client.get(reverse("run", args=[run.pk]) + "?show=everything")
    assert response.status_code == 400
    text = html.unescape(response.content.decode())
    assert 'data-error="request"' in text and "'everything'" in text


@pytest.mark.django_db
def test_a_run_s_outcomes_download_as_csv(admin_client: Client, run: CalculationRun) -> None:
    response = admin_client.get(reverse("run-outcomes-csv", args=[run.pk]))
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert f'filename="run-{run.pk}-outcomes.csv"' in response["Content-Disposition"]
    expected = io.StringIO()
    writer = csv.writer(expected, lineterminator="\r\n")  # RFC 4180
    writer.writerow(
        [
            "record_id",
            "subject",
            "reading",
            "outcome",
            "calculation_key",
            "refusal_class",
            "refusal_message",
        ]
    )
    for outcome in run.outcomes.order_by("record__pk"):
        writer.writerow(
            [
                outcome.record.pk,
                outcome.record.subject.label,
                outcome.record.reading,
                "refusal" if outcome.chart_id is None else "chart",
                outcome.chart_id or "",
                outcome.refusal_class,
                outcome.refusal_message,
            ]
        )
    assert response.content.decode() == expected.getvalue()


@pytest.mark.django_db
@pytest.mark.parametrize("at_a_time", [1, 50])  # documents read per query
def test_a_run_s_charts_download_as_json_lines_exactly_as_stored(
    admin_client: Client, run: CalculationRun, monkeypatch: pytest.MonkeyPatch, at_a_time: int
) -> None:
    monkeypatch.setattr(runs_views, "DOCUMENTS_AT_A_TIME", at_a_time)
    response = admin_client.get(reverse("run-charts-jsonl", args=[run.pk]))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/x-ndjson"
    assert f'filename="run-{run.pk}-charts.jsonl"' in response["Content-Disposition"]
    stored = [
        outcome.chart.document
        for outcome in run.outcomes.order_by("record__pk")
        if outcome.chart is not None
    ]
    assert len(stored) == 2
    text = body(response)
    assert text == "".join(f"{document}\n" for document in stored)
    for line in text.splitlines():
        chart_from_json(line)  # each line reads back as a chart


@pytest.mark.django_db
def test_records_with_the_same_inputs_share_one_chart_in_the_download(
    admin_client: Client, store: Callable[..., Record], eph: Ephemeris
) -> None:
    other = Source.objects.create(citation="Another synthetic source")
    records = [store(FIRST), store(SECOND), store(FIRST, source=other)]
    shared = Dataset.objects.create(name="shared")
    shared.records.add(*records)
    run = run_dataset(shared, SETTINGS, eph)
    assert (run.computed, run.reused) == (2, 1)
    outcomes = list(run.outcomes.order_by("record__pk"))
    assert outcomes[0].chart_id == outcomes[2].chart_id != outcomes[1].chart_id
    csv_rows = list(
        csv.reader(io.StringIO(body(admin_client.get(reverse("run-outcomes-csv", args=[run.pk])))))
    )
    assert [r[4] for r in csv_rows[1:]] == [o.chart_id for o in outcomes]
    lines = body(admin_client.get(reverse("run-charts-jsonl", args=[run.pk]))).splitlines()
    assert [chart_from_json(line).calculation_key for line in lines] == [
        outcomes[0].chart_id,
        outcomes[1].chart_id,
    ]


@pytest.mark.django_db
def test_an_unknown_run_or_dataset_is_not_found(admin_client: Client) -> None:
    for name in ("dataset", "run", "run-outcomes-csv", "run-charts-jsonl"):
        assert admin_client.get(reverse(name, args=[999_999])).status_code == 404, name
    assert admin_client.post(reverse("dataset-run", args=[999_999]), FORM).status_code == 404


# --- A stored chart -------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_stored_chart_shows_what_the_run_stored(
    admin_client: Client, run: CalculationRun
) -> None:
    stored = StoredChart.objects.order_by("stored_at").first()
    assert stored is not None
    chart = chart_from_json(stored.document)
    text = page(admin_client, reverse("stored-chart", args=[stored.pk]))
    assert chart.calculation_key in text
    assert json.loads(stored.document)["inputs"]["engine"]["version"] in text
    for body in chart.positions.bodies:
        assert f"{body.longitude:.6f}" in text
    assert len(re.findall(r'data-pair="', text)) == len(chart.pairs)
    assert 'name="aspects"' not in text  # a stored chart's settings are fixed


@pytest.mark.django_db
def test_a_stored_chart_from_another_engine_version_says_so(
    admin_client: Client, run: CalculationRun
) -> None:
    stored = StoredChart.objects.first()
    assert stored is not None
    document = json.loads(stored.document)
    document["inputs"]["engine"]["version"] = "0.0.1"
    inputs = json.dumps(document["inputs"], sort_keys=True, separators=(",", ":"))
    document["calculation_key"] = hashlib.sha256(inputs.encode()).hexdigest()
    older = StoredChart.objects.create(
        calculation_key=document["calculation_key"],
        document=json.dumps(document, separators=(",", ":")),
    )
    text = page(admin_client, reverse("stored-chart", args=[older.pk]))
    assert "0.0.1" in text and f"the running engine is {ENGINE_VERSION}" in text
    assert "the running engine is" not in page(
        admin_client, reverse("stored-chart", args=[stored.pk])
    )


@pytest.mark.django_db
def test_a_stored_document_this_engine_can_t_read_shows_why(admin_client: Client) -> None:
    unreadable = StoredChart.objects.create(calculation_key="f" * 64, document="{}")
    response = admin_client.get(reverse("stored-chart", args=[unreadable.pk]))
    assert response.status_code == 422
    text = html.unescape(response.content.decode())
    assert "InvalidChartDocument" in text and "data-pair" not in text


@pytest.mark.django_db
def test_an_unknown_stored_chart_is_not_found(admin_client: Client) -> None:
    for address in (reverse("stored-chart", args=["0" * 64]), "/charts/not-a-key/"):
        assert admin_client.get(address).status_code == 404, address
