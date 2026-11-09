"""A record's chart (page, pairs fragment, JSON document) and two records' comparison.

Each computes its charts on request (about 0.5 ms each) under the settings in the address,
`aspects` and `time_scale`, so an address reproduces what it shows. With no aspect angles named,
a chart has none: pairs show their separations and declinations, but no deviations. Settings the
engine can't use are a 400; a record the engine refuses shows the refusal instead of a chart.

A comparison is B minus A for every number (`compare()`), both charts under the same settings.
"""

import functools
from dataclasses import dataclass

from django.conf import settings as django_settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from astro_research.chart import Chart, ChartSettings, compute_chart
from astro_research.comparison import ChartComparison, PairChange, compare
from astro_research.ephemeris import Ephemeris
from astro_research.measurements import PairMeasurement
from astro_research.result_format import chart_to_json
from research_platform.chart_settings import (
    DEFAULT_TIME_SCALE,
    TIME_SCALES,
    InvalidSettings,
    chart_settings,
)
from research_platform.invalid_requests import InvalidRequest, download, fragment
from research_platform.models import Record
from research_platform.runner import REFUSALS

SORTS = ("pair", "deviation")
COMPARISON_SORTS = ("pair", "change", "deviation")
ANGLES = "angles"  # `involving=angles`: the pairs with the Ascendant, MC or Vertex


@dataclass(frozen=True)
class Refusal:
    name: str
    message: str


@dataclass(frozen=True)
class PairFilter:
    point: str  # a point's name, or "" for every pair
    angle: float | None  # the aspect angle whose deviation sorts the pairs
    sort: str


@dataclass(frozen=True)
class ComparisonFilter:
    involving: str  # "", ANGLES, or a point's name
    angle: float | None  # the aspect angle whose closer deviation sorts the pairs
    sort: str


@dataclass(frozen=True)
class RefusedRecord:
    record: Record
    refusal: Refusal


@login_required
def record_chart(request: HttpRequest, pk: int) -> HttpResponse:
    record = get_object_or_404(Record, pk=pk)
    context = {"record": record, **_form_values(request)}
    try:
        settings = _settings(request)
        chart, refusal = _chart(record, settings)
        pair_filter = _pair_filter(request, chart, settings)
    except InvalidRequest as error:
        return _refused_page(request, "research_platform/chart.html", context, error)
    context |= {
        "settings": settings,
        "chart": chart,
        "refusal": refusal,
        "filter": pair_filter,
        "pairs": _pairs(chart, pair_filter) if chart else (),
    }
    return render(request, "research_platform/chart.html", context)


@login_required
@fragment
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
@download
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
        raise InvalidRequest(str(error)) from error


def _form_values(request: HttpRequest) -> dict[str, object]:
    """What the settings form shows: the values as submitted, valid or not."""
    return {
        "aspects": request.GET.get("aspects", ""),
        "time_scale": request.GET.get("time_scale", DEFAULT_TIME_SCALE),
        "time_scales": sorted(TIME_SCALES),
        "query": request.GET.urlencode(),
    }


def _refused_page(
    request: HttpRequest, template: str, context: dict[str, object], error: InvalidRequest
) -> HttpResponse:
    """The page with its forms as submitted and the reason above them, and no results."""
    return render(request, template, {**context, "error": str(error)}, status=400)


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
        raise InvalidRequest(f"sort {sort!r} is not one of {', '.join(SORTS)}")
    if chart is not None and point and point not in {p.point.value for p in chart.points}:
        raise InvalidRequest(f"point {point!r} is not one of this chart's points")
    angle = _angle(angle_text, settings, sort)
    return PairFilter(point=point, angle=angle, sort=sort)


def _angle(text: str, settings: ChartSettings, sort: str) -> float | None:
    """The aspect angle named in `text`, which must be one of the settings'; needed to sort by
    deviation."""
    angle = None
    if text:
        try:
            angle = float(text)
        except ValueError:
            raise InvalidRequest(f"angle {text!r} is not a number") from None
        if angle not in settings.aspect_angles:
            raise InvalidRequest(f"angle {angle:g} is not one of the aspect angles named")
    if sort == "deviation" and angle is None:
        raise InvalidRequest("sorting by deviation needs an aspect angle")
    return angle


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


# --- Comparing two records ------------------------------------------------------------------


@login_required
def record_compare(request: HttpRequest, a: int, b: int) -> HttpResponse:
    records = (get_object_or_404(Record, pk=a), get_object_or_404(Record, pk=b))
    context = {"a": records[0], "b": records[1], **_form_values(request)}
    try:
        settings = _settings(request)
        comparison, refused = _comparison(records, settings)
        comparison_filter = _comparison_filter(request, comparison, settings)
    except InvalidRequest as error:
        return _refused_page(request, "research_platform/compare.html", context, error)
    context |= {
        "settings": settings,
        "comparison": comparison,
        "refused": refused,
        "filter": comparison_filter,
        "pairs": _comparison_pairs(comparison, comparison_filter) if comparison else (),
    }
    return render(request, "research_platform/compare.html", context)


@login_required
@fragment
def record_compare_pairs(request: HttpRequest, a: int, b: int) -> HttpResponse:
    records = (get_object_or_404(Record, pk=a), get_object_or_404(Record, pk=b))
    settings = _settings(request)
    comparison, refused = _comparison(records, settings)
    if comparison is None:
        refusals = {"refused": refused}
        return render(request, "research_platform/refused_records.html", refusals, status=422)
    comparison_filter = _comparison_filter(request, comparison, settings)
    context = {"settings": settings, "pairs": _comparison_pairs(comparison, comparison_filter)}
    return render(request, "research_platform/compare_pairs.html", context)


def _comparison(
    records: tuple[Record, Record], settings: ChartSettings
) -> tuple[ChartComparison | None, list[RefusedRecord]]:
    """B minus A, or the records the engine refused."""
    charts = []
    refused = []
    for record in records:
        chart, refusal = _chart(record, settings)
        if refusal is not None:
            refused.append(RefusedRecord(record, refusal))
        charts.append(chart)
    a, b = charts
    if a is None or b is None:
        return None, refused
    return compare(a, b), []


def _comparison_filter(
    request: HttpRequest, comparison: ChartComparison | None, settings: ChartSettings
) -> ComparisonFilter:
    involving = request.GET.get("involving", "")
    sort = request.GET.get("sort", "pair")
    if sort not in COMPARISON_SORTS:
        raise InvalidRequest(f"sort {sort!r} is not one of {', '.join(COMPARISON_SORTS)}")
    points = {change.point.value for change in comparison.points} if comparison else set()
    if comparison is not None and involving not in ("", ANGLES, *points):
        raise InvalidRequest(f"involving {involving!r} is neither {ANGLES!r} nor one of the points")
    angle = _angle(request.GET.get("angle", ""), settings, sort)
    return ComparisonFilter(involving=involving, angle=angle, sort=sort)


def _comparison_pairs(
    comparison: ChartComparison, comparison_filter: ComparisonFilter
) -> list[PairChange]:
    involving = comparison_filter.involving
    if involving == ANGLES:
        pairs = list(comparison.involving_angles())
    else:
        pairs = [
            change
            for change in comparison.pairs
            if not involving or involving in (change.a.a.point.value, change.a.b.point.value)
        ]
    match comparison_filter.sort:
        case "change":
            pairs.sort(key=lambda change: -abs(change.separation))
        case "deviation":
            assert comparison_filter.angle is not None  # _angle refuses a sort without one
            index = comparison.a.settings.aspect_angles.index(comparison_filter.angle)

            def closer(change: PairChange) -> float:
                deviation = change.deviations[index]
                return min(abs(deviation.a.deviation), abs(deviation.b.deviation))

            pairs.sort(key=closer)
    return pairs
