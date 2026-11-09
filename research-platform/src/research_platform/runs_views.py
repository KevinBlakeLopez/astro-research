"""Datasets, their runs, and the charts the runs stored.

A run started from a dataset's page runs there and then, in the request, and opens when it's
done, finished or failed. Above `RUN_LIMIT` records a dataset is left to `compute_dataset`:
500 records take 4.1-4.3 s from the form when every chart is computed, and 0.6 s run again
(measured). Runs are never changed or deleted here; a run is the record of what was computed.

A run's outcomes download as CSV, one row per record. Its charts download as JSON Lines: each
stored chart once, in the order of its first record, exactly as stored. Records with the same
inputs share a chart, and the CSV says which. A chart is about 116 KiB (500 came to 56.5 MiB,
measured), so that download is streamed, 50 charts at a time.

A stored chart's page reads its document back (`chart_from_json`) and shows it with the chart
page's tables. Its settings are fixed, and the engine version is the one that computed it, which
may not be the one running.
"""

import csv
import io
import json
import logging
import shlex
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, QuerySet
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from astro_research.chart import ENGINE_VERSION, ChartSettings
from astro_research.result_format import InvalidChartDocument
from research_platform.chart_settings import (
    DEFAULT_TIME_SCALE,
    TIME_SCALES,
    InvalidSettings,
    chart_settings,
    time_scale_name,
)
from research_platform.invalid_requests import InvalidRequest
from research_platform.models import CalculationRun, Dataset, RunOutcome, StoredChart
from research_platform.runner import finish_run, start_run
from research_platform.views import Refusal, _ephemeris

logger = logging.getLogger(__name__)

RUN_LIMIT = 500  # records a run started from the page may have
SHOWS = {"": "every outcome", "charts": "charts only", "refusals": "refusals only"}
CSV_COLUMNS = (
    "record_id",
    "subject",
    "reading",
    "outcome",
    "calculation_key",
    "refusal_class",
    "refusal_message",
)
DOCUMENTS_AT_A_TIME = 50


@dataclass(frozen=True)
class DescribedRun:
    """A run with its settings as the run form takes them."""

    run: CalculationRun
    aspects: str  # e.g. "0, 60, 90, 120, 180"
    time_scale: str  # e.g. "ut1-from-iers"
    duration: timedelta | None


@dataclass(frozen=True)
class DatasetRow:
    dataset: Dataset
    size: int
    latest: CalculationRun | None


@login_required
def datasets(request: HttpRequest) -> HttpResponse:
    found = Dataset.objects.order_by("name").annotate(
        size=Count("records", distinct=True), latest_pk=Max("runs__pk")
    )
    latest = CalculationRun.objects.in_bulk([d.latest_pk for d in found if d.latest_pk])
    rows = [DatasetRow(d, d.size, latest.get(d.latest_pk)) for d in found]
    return render(request, "research_platform/datasets.html", {"rows": rows})


@login_required
def dataset(request: HttpRequest, pk: int) -> HttpResponse:
    chosen = get_object_or_404(Dataset, pk=pk)
    context = _dataset_context(chosen, "", DEFAULT_TIME_SCALE)
    return render(request, "research_platform/dataset.html", context)


@login_required
@require_POST
def dataset_run(request: HttpRequest, pk: int) -> HttpResponse:
    chosen = get_object_or_404(Dataset, pk=pk)
    aspects = request.POST.get("aspects", "")
    time_scale = request.POST.get("time_scale", DEFAULT_TIME_SCALE)
    try:
        settings = _run_settings(chosen, aspects, time_scale)
    except InvalidRequest as error:
        context = _dataset_context(chosen, aspects, time_scale) | {"error": str(error)}
        return render(request, "research_platform/dataset.html", context, status=400)
    eph = _ephemeris()
    run = start_run(chosen, settings, eph)
    try:
        finish_run(run, settings, eph)
    except Exception:
        # The run is recorded as failed, with the error, and its page shows it; the traceback
        # is kept in the server's log.
        logger.exception("run %s of dataset %s failed", run.pk, chosen.pk)
    return redirect("run", pk=run.pk)


@login_required
def run(request: HttpRequest, pk: int) -> HttpResponse:
    chosen = get_object_or_404(CalculationRun.objects.select_related("dataset"), pk=pk)
    show = request.GET.get("show", "")
    context = {
        "described": _described(chosen),
        "run": chosen,
        "provenance": chosen.ephemeris_provenance(),
        "points": ", ".join(point.value for point in chosen.chart_settings().points),
        "show": show,
        "shows": SHOWS,
    }
    if show not in SHOWS:
        error = f"show {show!r} is not one of {', '.join(name for name in SHOWS if name)}"
        context |= {"error": error, "outcomes": ()}
        return render(request, "research_platform/run.html", context, status=400)
    context["outcomes"] = _outcomes(chosen, show)
    return render(request, "research_platform/run.html", context)


@login_required
def run_outcomes_csv(request: HttpRequest, pk: int) -> HttpResponse:
    chosen = get_object_or_404(CalculationRun, pk=pk)
    buffer = io.StringIO()
    writer = csv.writer(buffer)  # RFC 4180: \r\n line endings, quoted only where needed
    writer.writerow(CSV_COLUMNS)
    for outcome in _outcomes(chosen, ""):
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
    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="run-{chosen.pk}-outcomes.csv"'
    return response


@login_required
def run_charts_jsonl(request: HttpRequest, pk: int) -> StreamingHttpResponse:
    chosen = get_object_or_404(CalculationRun, pk=pk)
    keys = list(
        dict.fromkeys(  # each chart once, in the order of its first record
            chosen.outcomes.exclude(chart=None)
            .order_by("record__pk")
            .values_list("chart_id", flat=True)
        )
    )
    response = StreamingHttpResponse(_documents(keys), content_type="application/x-ndjson")
    response["Content-Disposition"] = f'attachment; filename="run-{chosen.pk}-charts.jsonl"'
    return response


@login_required
def stored_chart(request: HttpRequest, key: str) -> HttpResponse:
    stored = get_object_or_404(StoredChart, pk=key)
    context: dict[str, object] = {"stored": stored, "running_version": ENGINE_VERSION}
    try:
        chart = stored.chart()
    except InvalidChartDocument as error:
        context["refusal"] = Refusal(type(error).__name__, str(error))
        return render(request, "research_platform/stored_chart.html", context, status=422)
    settings = chart.settings
    context |= {
        "chart": chart,
        "settings": settings,
        "pairs": chart.pairs,
        "engine_version": json.loads(stored.document)["inputs"]["engine"]["version"],
        "aspects": _aspects_text(settings),
        "time_scale": time_scale_name(settings.time_scale),
    }
    return render(request, "research_platform/stored_chart.html", context)


def _dataset_context(chosen: Dataset, aspects: str, time_scale: str) -> dict[str, object]:
    """The dataset's page, with the run form showing `aspects` and `time_scale`."""
    records = chosen.records.select_related("subject", "source").order_by("pk")
    runs = chosen.runs.order_by("-pk")  # ids increase as runs start: newest first
    return {
        "dataset": chosen,
        "records": records,
        "size": len(records),
        "runs": [_described(each) for each in runs],
        "aspects": aspects,
        "time_scale": time_scale,
        "time_scales": sorted(TIME_SCALES),
        "limit": RUN_LIMIT,
    }


def _run_settings(chosen: Dataset, aspects: str, time_scale: str) -> ChartSettings:
    try:
        settings = chart_settings(aspects, time_scale)
    except InvalidSettings as error:
        raise InvalidRequest(str(error)) from error
    size = chosen.records.count()
    if size > RUN_LIMIT:
        raise InvalidRequest(
            f"{chosen.name} has {size} records, and a run started here may have at most "
            f"{RUN_LIMIT}. Run it with: python manage.py compute_dataset "
            f"{shlex.quote(chosen.name)} --aspects {shlex.quote(aspects)} --time-scale {time_scale}"
        )
    return settings


def _described(chosen: CalculationRun) -> DescribedRun:
    settings = chosen.chart_settings()
    duration = None
    if chosen.finished_at is not None:
        duration = chosen.finished_at - chosen.started_at
    return DescribedRun(
        run=chosen,
        aspects=_aspects_text(settings),
        time_scale=time_scale_name(settings.time_scale),
        duration=duration,
    )


def _aspects_text(settings: ChartSettings) -> str:
    return ", ".join(f"{angle:g}" for angle in settings.aspect_angles)


def _outcomes(chosen: CalculationRun, show: str) -> QuerySet[RunOutcome]:
    """The run's outcomes by record, without their charts' documents (about 138 KiB each)."""
    outcomes = chosen.outcomes.select_related("record__subject").order_by("record__pk")
    if show == "charts":
        return outcomes.exclude(chart=None)
    if show == "refusals":
        return outcomes.filter(chart=None)
    return outcomes


def _documents(keys: list[str]) -> Iterator[str]:
    for start in range(0, len(keys), DOCUMENTS_AT_A_TIME):
        chunk = keys[start : start + DOCUMENTS_AT_A_TIME]
        documents = dict(StoredChart.objects.filter(pk__in=chunk).values_list("pk", "document"))
        for key in chunk:
            yield f"{documents[key]}\n"
