"""A record's birth-time sensitivity: the page, its series and detail fragments, and its CSV.

Each runs the engine's `sweep()` on request, under the settings in the address and the offsets
named by `span` and `step`, in seconds: `span=600&step=60` is -10 to +10 minutes every minute.
Offsets are never defaulted. A sweep is capped at 241 offsets and a 2-hour span: about 0.3 s of
computing (extrapolated from 139 ms for 121 offsets, measured), and well inside the hours it
takes the Ascendant to move half a circle, past which the sweep's wrapped changes would misread.

The series are listed from the widest range of change to the narrowest (`by_range()`), the
first 100 unless `limit` says otherwise. Nothing marks a series as sensitive: the range is the
number.
"""

import csv
import io
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from astro_research.chart import ChartSettings
from astro_research.sensitivity import MeasurementSeries, Quantity, SensitivitySweep, sweep
from research_platform.invalid_requests import InvalidRequest, download, fragment
from research_platform.models import Record
from research_platform.runner import REFUSALS
from research_platform.views import (
    Refusal,
    _angle,
    _ephemeris,
    _form_values,
    _refused_page,
    _settings,
)

MAX_SPAN_S = 7200
MAX_OFFSETS = 241
LIST_LIMIT = 100
ROTATIONS = ("", "only", "none")
CSV_COLUMNS = (
    "offset_s",
    "quantity",
    "point_a",
    "point_b",
    "aspect_angle",
    "rotation_driven",
    "value",
    "change",
)
PLOT_WIDTH, PLOT_HEIGHT, PLOT_MARGIN = 640, 180, 12


@dataclass(frozen=True)
class Offsets:
    span: int  # seconds either side of the recorded time
    step: int  # seconds

    def deltas(self) -> list[timedelta]:
        count = self.span // self.step
        return [timedelta(seconds=k * self.step) for k in range(-count, count + 1)]


@dataclass(frozen=True)
class SeriesFilter:
    quantity: str
    involving: str
    angle: float | None
    rotation: str
    limit: int | None  # None for every series


@dataclass(frozen=True)
class SeriesRow:
    series: MeasurementSeries
    id: str  # quantity|a|b|angle, b and angle empty when absent
    detail_query: str


@login_required
def record_sensitivity(request: HttpRequest, pk: int) -> HttpResponse:
    record = get_object_or_404(Record, pk=pk)
    context = {
        "record": record,
        **_form_values(request),
        "span": request.GET.get("span", ""),
        "step": request.GET.get("step", ""),
    }
    try:
        settings = _settings(request)
        offsets = _offsets(request, required=False)
        swept, refusal = _sweep(record, settings, offsets) if offsets else (None, None)
        series_filter = _series_filter(request, swept, settings)
    except InvalidRequest as error:
        return _refused_page(request, "research_platform/sensitivity.html", context, error)
    context |= {
        "settings": settings,
        "offsets": offsets,
        "sweep": swept,
        "refusal": refusal,
        "filter": series_filter,
        "quantities": [quantity.value for quantity in Quantity],
        "rows": _rows(request, swept, series_filter) if swept else [],
        "total": len(swept.series) if swept else 0,
    }
    return render(request, "research_platform/sensitivity.html", context)


@login_required
@fragment
def record_sensitivity_series(request: HttpRequest, pk: int) -> HttpResponse:
    record = get_object_or_404(Record, pk=pk)
    settings = _settings(request)
    offsets = _offsets(request, required=True)
    assert offsets is not None
    swept, refusal = _sweep(record, settings, offsets)
    if swept is None:
        refused = {"refusal": refusal}
        return render(request, "research_platform/refusal.html", refused, status=422)
    series_filter = _series_filter(request, swept, settings)
    context = {
        "rows": _rows(request, swept, series_filter),
        "total": len(swept.series),
        "record_pk": record.pk,
    }
    return render(request, "research_platform/sensitivity_series.html", context)


@login_required
@fragment
def record_sensitivity_detail(request: HttpRequest, pk: int) -> HttpResponse:
    record = get_object_or_404(Record, pk=pk)
    settings = _settings(request)
    offsets = _offsets(request, required=True)
    assert offsets is not None
    swept, refusal = _sweep(record, settings, offsets)
    if swept is None:
        refused = {"refusal": refusal}
        return render(request, "research_platform/refusal.html", refused, status=422)
    series = _one_series(request, swept)
    seconds = [int(offset.total_seconds()) for offset in swept.offsets]
    context = {
        "series": series,
        "name": _series_id(series),
        "steps": list(zip(seconds, series.values, series.changes, strict=True)),
        "plot": _plot(seconds, series.changes),
        "plot_width": PLOT_WIDTH,
        "plot_height": PLOT_HEIGHT,
    }
    return render(request, "research_platform/sensitivity_detail.html", context)


@login_required
@download
def record_sensitivity_csv(request: HttpRequest, pk: int) -> HttpResponse:
    record = get_object_or_404(Record, pk=pk)
    settings = _settings(request)
    offsets = _offsets(request, required=True)
    assert offsets is not None
    swept, refusal = _sweep(record, settings, offsets)
    if swept is None:
        assert refusal is not None
        text = f"{refusal.name}: {refusal.message}\n"
        return HttpResponse(text, status=422, content_type="text/plain")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for row in swept.rows():
        writer.writerow(
            [
                repr(row.offset_s),
                row.quantity.value,
                row.point_a.value,
                "" if row.point_b is None else row.point_b.value,
                "" if row.aspect_angle is None else repr(row.aspect_angle),
                "true" if row.rotation_driven else "false",
                repr(row.value),  # Python's shortest repr reads back exactly
                repr(row.change),
            ]
        )
    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    name = f"{swept.base.calculation_key}-span{offsets.span}-step{offsets.step}.csv"
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


def _offsets(request: HttpRequest, *, required: bool) -> Offsets | None:
    span_text, step_text = request.GET.get("span"), request.GET.get("step")
    if span_text is None and step_text is None and not required:
        return None
    if span_text is None or step_text is None:
        raise InvalidRequest("name both a span and a step, in seconds")
    try:
        span, step = int(span_text), int(step_text)
    except ValueError:
        raise InvalidRequest("the span and step are whole seconds") from None
    if span <= 0 or step <= 0:
        raise InvalidRequest("the span and step must be positive")
    if span % step:
        raise InvalidRequest(f"the span ({span} s) must be a whole number of steps ({step} s)")
    if span > MAX_SPAN_S:
        raise InvalidRequest(f"the span is at most {MAX_SPAN_S} s either side")
    if 2 * (span // step) + 1 > MAX_OFFSETS:
        raise InvalidRequest(f"a sweep is at most {MAX_OFFSETS} offsets")
    return Offsets(span=span, step=step)


def _sweep(
    record: Record, settings: ChartSettings, offsets: Offsets
) -> tuple[SensitivitySweep | None, Refusal | None]:
    """The record's sweep, or the engine's refusal of the record or of any offset's chart."""
    try:
        return sweep(_ephemeris(), record.birth_record(), settings, offsets.deltas()), None
    except REFUSALS as refusal:
        return None, Refusal(type(refusal).__name__, str(refusal))


def _series_filter(
    request: HttpRequest, swept: SensitivitySweep | None, settings: ChartSettings
) -> SeriesFilter:
    quantity = request.GET.get("quantity", "")
    if quantity and quantity not in {member.value for member in Quantity}:
        raise InvalidRequest(f"quantity {quantity!r} is not one the sweep measures")
    involving = request.GET.get("involving", "")
    points = {point.point.value for point in swept.base.points} if swept else set()
    if swept is not None and involving and involving not in points:
        raise InvalidRequest(f"point {involving!r} is not one of this chart's points")
    rotation = request.GET.get("rotation", "")
    if rotation not in ROTATIONS:
        raise InvalidRequest(f"rotation {rotation!r} is not 'only' or 'none'")
    limit_text = request.GET.get("limit", "")
    limit: int | None = LIST_LIMIT
    if limit_text == "all":
        limit = None
    elif limit_text:
        if not limit_text.isdigit() or int(limit_text) == 0:
            raise InvalidRequest(f"limit {limit_text!r} is neither 'all' nor a positive number")
        limit = int(limit_text)
    angle = _angle(request.GET.get("angle", ""), settings, "")
    return SeriesFilter(quantity, involving, angle, rotation, limit)


def _rows(
    request: HttpRequest, swept: SensitivitySweep, series_filter: SeriesFilter
) -> list[SeriesRow]:
    chosen = [
        series
        for series in swept.by_range()
        if (not series_filter.quantity or series.quantity.value == series_filter.quantity)
        and (series_filter.angle is None or series.aspect_angle == series_filter.angle)
        and (
            not series_filter.involving
            or series_filter.involving in {point.value for point in series.points}
        )
        and (
            series_filter.rotation == ""
            or series.rotation_driven == (series_filter.rotation == "only")
        )
    ]
    if series_filter.limit is not None:
        chosen = chosen[: series_filter.limit]
    settings_query = {
        name: request.GET[name]
        for name in ("aspects", "time_scale", "span", "step")
        if name in request.GET
    }
    rows = []
    for series in chosen:
        quantity, a, b, angle = _series_id(series).split("|")
        identity = {"quantity": quantity, "a": a, "b": b, "angle": angle}
        detail = {**settings_query, **{name: value for name, value in identity.items() if value}}
        rows.append(SeriesRow(series, _series_id(series), urlencode(detail)))
    return rows


def _one_series(request: HttpRequest, swept: SensitivitySweep) -> MeasurementSeries:
    quantity, a, b = (request.GET.get(name, "") for name in ("quantity", "a", "b"))
    angle_text = request.GET.get("angle", "")
    try:
        angle = float(angle_text) if angle_text else None
    except ValueError:
        raise InvalidRequest(f"angle {angle_text!r} is not a number") from None
    for series in swept.series:
        names = [point.value for point in series.points]
        if (
            series.quantity.value == quantity
            and names == ([a, b] if b else [a])
            and series.aspect_angle == angle
        ):
            return series
    raise InvalidRequest("the sweep has no such series")


def _series_id(series: MeasurementSeries) -> str:
    a = series.points[0].value
    b = series.points[1].value if len(series.points) == 2 else ""
    angle = "" if series.aspect_angle is None else repr(series.aspect_angle)
    return f"{series.quantity.value}|{a}|{b}|{angle}"


def _plot(seconds: list[int], changes: tuple[float, ...]) -> str:
    """SVG polyline points for change (arcsec) against offset, inside the plot's margins."""
    low, high = min(changes), max(changes)
    spread = (high - low) or 1.0  # a flat series is drawn as a flat line
    first, last = seconds[0], seconds[-1]
    width = PLOT_WIDTH - 2 * PLOT_MARGIN
    height = PLOT_HEIGHT - 2 * PLOT_MARGIN
    points = []
    for second, change in zip(seconds, changes, strict=True):
        x = PLOT_MARGIN + width * (second - first) / (last - first or 1)
        y = PLOT_MARGIN + height * (1 - (change - low) / spread)
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)
