"""A record's chart: the page, its pairs fragment, and its JSON document.

Each computes the chart on request (about 0.5 ms) under the settings in the address, `aspects`
and `time_scale`, so an address reproduces what it shows. With no aspect angles named, the chart
has none: pairs show their separations and declinations, but no deviations. Settings the engine
can't use are a 400; a record the engine refuses shows the refusal instead of a chart.
"""

import functools
from dataclasses import dataclass

from django.conf import settings as django_settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import BadRequest
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from astro_research.chart import Chart, ChartSettings, compute_chart
from astro_research.ephemeris import Ephemeris
from astro_research.measurements import PairMeasurement
from astro_research.result_format import chart_to_json
from research_platform.chart_settings import (
    DEFAULT_TIME_SCALE,
    TIME_SCALES,
    InvalidSettings,
    chart_settings,
)
from research_platform.models import Record
from research_platform.runner import REFUSALS

SORTS = ("pair", "deviation")


@dataclass(frozen=True)
class Refusal:
    name: str
    message: str


@dataclass(frozen=True)
class PairFilter:
    point: str  # a point's name, or "" for every pair
    angle: float | None  # the aspect angle whose deviation sorts the pairs
    sort: str


@login_required
def record_chart(request: HttpRequest, pk: int) -> HttpResponse:
    record = get_object_or_404(Record, pk=pk)
    settings = _settings(request)
    chart, refusal = _chart(record, settings)
    pair_filter = _pair_filter(request, chart, settings)
    context = {
        "record": record,
        "settings": settings,
        "aspects": request.GET.get("aspects", ""),
        "time_scale": request.GET.get("time_scale", DEFAULT_TIME_SCALE),
        "time_scales": sorted(TIME_SCALES),
        "chart": chart,
        "refusal": refusal,
        "filter": pair_filter,
        "pairs": _pairs(chart, pair_filter) if chart else (),
    }
    return render(request, "research_platform/chart.html", context)


@login_required
def record_chart_pairs(request: HttpRequest, pk: int) -> HttpResponse:
    record = get_object_or_404(Record, pk=pk)
    settings = _settings(request)
    chart, refusal = _chart(record, settings)
    if chart is None:
        return render(request, "research_platform/refusal.html", {"refusal": refusal}, status=422)
    pair_filter = _pair_filter(request, chart, settings)
    context = {"settings": settings, "pairs": _pairs(chart, pair_filter)}
    return render(request, "research_platform/pairs.html", context)


@login_required
def record_chart_json(request: HttpRequest, pk: int) -> HttpResponse:
    record = get_object_or_404(Record, pk=pk)
    chart, refusal = _chart(record, _settings(request))
    if chart is None:
        assert refusal is not None
        return HttpResponse(
            f"{refusal.name}: {refusal.message}\n", status=422, content_type="text/plain"
        )
    response = HttpResponse(chart_to_json(chart), content_type="application/json")
    response["Content-Disposition"] = f'attachment; filename="{chart.calculation_key}.json"'
    return response


@functools.cache
def _ephemeris() -> Ephemeris:
    """One verified handle per process, opened on first use."""
    return Ephemeris.open(django_settings.EPHE_DIR)


def _settings(request: HttpRequest) -> ChartSettings:
    try:
        return chart_settings(
            request.GET.get("aspects", ""), request.GET.get("time_scale", DEFAULT_TIME_SCALE)
        )
    except InvalidSettings as error:
        raise BadRequest(str(error)) from error


def _chart(record: Record, settings: ChartSettings) -> tuple[Chart | None, Refusal | None]:
    try:
        return compute_chart(_ephemeris(), record.birth_record(), settings), None
    except REFUSALS as refusal:
        return None, Refusal(type(refusal).__name__, str(refusal))


def _pair_filter(request: HttpRequest, chart: Chart | None, settings: ChartSettings) -> PairFilter:
    point = request.GET.get("point", "")
    angle_text = request.GET.get("angle", "")
    sort = request.GET.get("sort", "pair")
    if sort not in SORTS:
        raise BadRequest(f"sort {sort!r} is not one of {', '.join(SORTS)}")
    if chart is not None and point and point not in {p.point.value for p in chart.points}:
        raise BadRequest(f"point {point!r} is not one of this chart's points")
    angle = None
    if angle_text:
        try:
            angle = float(angle_text)
        except ValueError:
            raise BadRequest(f"angle {angle_text!r} is not a number") from None
        if angle not in settings.aspect_angles:
            raise BadRequest(f"angle {angle:g} is not one of the aspect angles named")
    if sort == "deviation" and angle is None:
        raise BadRequest("sorting by deviation needs an aspect angle")
    return PairFilter(point=point, angle=angle, sort=sort)


def _pairs(chart: Chart, pair_filter: PairFilter) -> list[PairMeasurement]:
    pairs = [
        pair
        for pair in chart.pairs
        if not pair_filter.point or pair_filter.point in (pair.a.point.value, pair.b.point.value)
    ]
    if pair_filter.sort == "deviation":
        assert pair_filter.angle is not None  # _pair_filter refuses a deviation sort without one
        index = chart.settings.aspect_angles.index(pair_filter.angle)
        pairs.sort(key=lambda pair: abs(pair.deviations[index].deviation))
    return pairs
