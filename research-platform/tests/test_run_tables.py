"""A run's measurements as tidy tables: three CSV downloads, built from its stored charts.

Each table has a row per record with a chart, in record order, led by `record_id`, `subject` and
`calculation_key`, so the tables join with each other and with the outcomes CSV:

- chart values: one row per record (time scales, ARMC, obliquity, the polar circle);
- points: one row per record and point, in the chart's order (17 points);
- pairs: one row per record and pair, in the chart's order (136 pairs).

Values are Python's repr, which reads back exactly; booleans are `true` or `false`. Deviations
from aspect angles aren't written: each is exactly separation - angle, with the separation's
rate (272,000 checked after a JSON round trip). Refused records have no rows, and records that
share a chart each get its rows. Documents are read a batch at a time, and a document this
engine can't read refuses the table (422) before anything is sent.
"""

import csv
import html
import io
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from astro_research.chart import BirthRecord, Chart, ChartSettings
from astro_research.civil_time import UtcTime, ZonedLocalTime
from astro_research.ephemeris import Ephemeris, EphemerisNotLoaded
from astro_research.geo import GeoPosition
from astro_research.measurements import MAJOR
from research_platform import runner, runs_views
from research_platform.models import CalculationRun, Dataset, Record, Source, StoredChart, Subject
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
LEAD = ["record_id", "subject", "calculation_key"]
CHART_VALUES = [
    *LEAD,
    "utc",
    "jd_ut1",
    "jd_tt",
    "delta_t_s",
    "ut1_minus_utc_s",
    "armc",
    "true_obliquity",
    "beyond_polar_circle",
]
POINTS = [
    *LEAD,
    "point",
    "longitude",
    "latitude",
    "declination",
    "longitude_speed",
    "declination_speed",
]
PAIRS = [
    *LEAD,
    "point_a",
    "point_b",
    "longitude_difference",
    "separation",
    "separation_rate",
    "great_circle_separation",
    "declination_difference",
    "declination_sum",
    "declination_difference_rate",
    "declination_sum_rate",
]
TABLES = {
    "run-chart-values-csv": "chart-values",
    "run-points-csv": "points",
    "run-pairs-csv": "pairs",
}


@pytest.fixture
def run(store: Callable[..., Record], eph: Ephemeris) -> CalculationRun:
    """Three records of three subjects: two charts and one refusal (observed)."""
    records = [
        store(FIRST),
        store(AMBIGUOUS, subject=Subject.objects.create(label="subject-2")),
        store(SECOND, subject=Subject.objects.create(label="subject-3")),
    ]
    dataset = Dataset.objects.create(name="sample")
    dataset.records.add(*records)
    return run_dataset(dataset, SETTINGS, eph)


@pytest.fixture(params=[1, 50])
def at_a_time(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> int:
    """Documents read per query; 1 puts a batch boundary between every chart."""
    monkeypatch.setattr(runs_views, "DOCUMENTS_AT_A_TIME", request.param)
    value: int = request.param
    return value


def download(client: Client, name: str, run: CalculationRun) -> str:
    response: Any = client.get(reverse(name, args=[run.pk]))  # streamed or not
    assert response.status_code == 200, response
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    filename = f"run-{run.pk}-{TABLES[name]}.csv"
    assert response["Content-Disposition"] == f'attachment; filename="{filename}"'
    content = b"".join(response.streaming_content) if response.streaming else response.content
    return content.decode()


def expected(header: list[str], rows: list[list[Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")  # RFC 4180
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def charted(run: CalculationRun) -> list[tuple[Record, Chart]]:
    """Each record with a chart, in record order, with its stored chart read back."""
    outcomes = run.outcomes.select_related("record__subject", "chart").order_by("record__pk")
    return [(o.record, o.chart.chart()) for o in outcomes if o.chart is not None]


def lead(record: Record, chart: Chart) -> list[Any]:
    return [record.pk, record.subject.label, chart.calculation_key]


def chart_value_rows(run: CalculationRun) -> list[list[Any]]:
    return [
        [
            *lead(record, chart),
            chart.time.instant.utc.isoformat(),
            repr(chart.time.jd_ut1),
            repr(chart.time.jd_tt),
            repr(chart.time.delta_t_s),
            repr(chart.time.ut1_minus_utc_s),
            repr(chart.angles.armc),
            repr(chart.angles.true_obliquity),
            "true" if chart.angles.beyond_polar_circle else "false",
        ]
        for record, chart in charted(run)
    ]


def point_rows(run: CalculationRun) -> list[list[Any]]:
    return [
        [
            *lead(record, chart),
            point.point.value,
            repr(point.longitude),
            repr(point.latitude),
            repr(point.declination),
            repr(point.longitude_speed),
            repr(point.declination_speed),
        ]
        for record, chart in charted(run)
        for point in chart.points
    ]


def pair_rows(run: CalculationRun) -> list[list[Any]]:
    return [
        [
            *lead(record, chart),
            pair.a.point.value,
            pair.b.point.value,
            repr(pair.longitude_difference),
            repr(pair.separation),
            repr(pair.separation_rate),
            repr(pair.great_circle_separation),
            repr(pair.declination_difference),
            repr(pair.declination_sum),
            repr(pair.declination_difference_rate),
            repr(pair.declination_sum_rate),
        ]
        for record, chart in charted(run)
        for pair in chart.pairs
    ]


def record_ids(text: str) -> set[str]:
    return {row[0] for row in list(csv.reader(io.StringIO(text)))[1:]}


# --- The tables -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_chart_values_are_a_row_per_record_with_a_chart(
    admin_client: Client, run: CalculationRun, at_a_time: int
) -> None:
    text = download(admin_client, "run-chart-values-csv", run)
    assert text == expected(CHART_VALUES, chart_value_rows(run))
    assert text.count("\r\n") == 1 + 2


@pytest.mark.django_db
def test_points_are_a_row_per_record_and_point(
    admin_client: Client, run: CalculationRun, at_a_time: int
) -> None:
    text = download(admin_client, "run-points-csv", run)
    assert text == expected(POINTS, point_rows(run))
    assert text.count("\r\n") == 1 + 2 * 17


@pytest.mark.django_db
def test_pairs_are_a_row_per_record_and_pair_without_deviations(
    admin_client: Client, run: CalculationRun, at_a_time: int
) -> None:
    text = download(admin_client, "run-pairs-csv", run)
    assert text == expected(PAIRS, pair_rows(run))
    assert text.count("\r\n") == 1 + 2 * 136
    assert "deviation" not in text.split("\r\n", 1)[0]


@pytest.mark.django_db
def test_values_read_back_as_the_stored_chart_s(admin_client: Client, run: CalculationRun) -> None:
    rows = list(csv.reader(io.StringIO(download(admin_client, "run-pairs-csv", run))))
    (record, chart), *_ = charted(run)
    first = dict(zip(rows[0], rows[1], strict=True))
    assert float(first["separation"]) == chart.pairs[0].separation
    assert float(first["great_circle_separation"]) == chart.pairs[0].great_circle_separation
    assert first["record_id"] == str(record.pk)


@pytest.mark.django_db
def test_refused_records_have_no_rows(admin_client: Client, run: CalculationRun) -> None:
    refused = run.outcomes.get(chart=None).record
    with_charts = {str(record.pk) for record, _ in charted(run)}
    for name in TABLES:
        assert record_ids(download(admin_client, name, run)) == with_charts, name
    assert str(refused.pk) not in with_charts


@pytest.mark.django_db
def test_records_sharing_a_chart_each_get_its_rows(
    admin_client: Client, store: Callable[..., Record], eph: Ephemeris
) -> None:
    other = Source.objects.create(citation="Another synthetic source")
    records = [store(FIRST), store(FIRST, source=other)]
    dataset = Dataset.objects.create(name="shared")
    dataset.records.add(*records)
    run = run_dataset(dataset, SETTINGS, eph)
    assert (run.computed, run.reused) == (1, 1)
    rows = list(csv.reader(io.StringIO(download(admin_client, "run-points-csv", run))))[1:]
    assert [row[0] for row in rows] == [str(records[0].pk)] * 17 + [str(records[1].pk)] * 17
    assert [row[2:] for row in rows[:17]] == [row[2:] for row in rows[17:]]


@pytest.mark.django_db
def test_a_failed_run_s_tables_have_only_their_headers(
    admin_client: Client, store: Callable[..., Record], monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(*args: Any, **kwargs: Any) -> Any:
        raise EphemerisNotLoaded("the files moved")

    dataset = Dataset.objects.create(name="failing")
    dataset.records.add(store(FIRST))
    monkeypatch.setattr(runner, "compute_chart", failing)
    with pytest.raises(EphemerisNotLoaded):
        run_dataset(dataset, SETTINGS)
    run = CalculationRun.objects.get(status=CalculationRun.Status.FAILED)
    for name, header in zip(TABLES, (CHART_VALUES, POINTS, PAIRS), strict=True):
        assert download(admin_client, name, run) == expected(header, []), name


@pytest.mark.django_db
def test_a_document_this_engine_can_t_read_refuses_the_table(
    admin_client: Client, run: CalculationRun, at_a_time: int
) -> None:
    *_, (_, chart) = charted(run)  # the last, so every batch is checked
    StoredChart.objects.filter(pk=chart.calculation_key).update(document="{}")
    for name in TABLES:
        response = admin_client.get(reverse(name, args=[run.pk]))
        assert response.status_code == 422, name
        assert response["Content-Type"] == "text/plain; charset=utf-8"
        text = response.content.decode()
        assert "InvalidChartDocument" in text and chart.calculation_key in text


# --- Getting there --------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_run_s_page_links_to_its_tables(admin_client: Client, run: CalculationRun) -> None:
    response = admin_client.get(reverse("run", args=[run.pk]))
    text = html.unescape(response.content.decode())
    for name in TABLES:
        assert f'href="{reverse(name, args=[run.pk])}"' in text, name


@pytest.mark.django_db
def test_the_tables_need_a_login(client: Client, run: CalculationRun) -> None:
    for name in TABLES:
        response = client.get(reverse(name, args=[run.pk]))
        assert response.status_code == 302 and "login" in response["Location"], name


@pytest.mark.django_db
def test_an_unknown_run_s_tables_are_not_found(admin_client: Client) -> None:
    for name in TABLES:
        assert admin_client.get(reverse(name, args=[999_999])).status_code == 404, name
