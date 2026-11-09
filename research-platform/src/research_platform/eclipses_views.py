"""A record's nearest eclipses: the solar and lunar eclipse before and after the birth.

The types come from the address, `solar` and `lunar`, each a comma-separated list of types or
`all`, and are never defaulted; a kind is shown only when its types are named. Each eclipse is
the engine's `previous_eclipse` or `next_eclipse` (about a millisecond for all four, measured).
An eclipse's degree, by the project's convention, is taken at the syzygy: the Sun's for a solar
eclipse, the Moon's for a lunar one. An eclipse the engine refuses, for example past IERS's last
day, shows its refusal in its own place, and the others still show.
"""

from collections.abc import Callable
from dataclasses import dataclass

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from astro_research.astronomical_time import AstronomicalTime, TimeScale
from astro_research.civil_time import resolve
from astro_research.eclipses import (
    LUNAR_TYPES,
    SOLAR_TYPES,
    Eclipse,
    EclipseKind,
    EclipseType,
    next_eclipse,
    previous_eclipse,
)
from astro_research.ephemeris import Ephemeris
from astro_research.positions import BodyPosition
from research_platform.chart_settings import DEFAULT_TIME_SCALE, TIME_SCALES
from research_platform.invalid_requests import InvalidRequest
from research_platform.models import Record
from research_platform.runner import REFUSALS
from research_platform.views import Refusal, _ephemeris, _form_values, _refused_page

KINDS = {"solar": (EclipseKind.SOLAR, SOLAR_TYPES), "lunar": (EclipseKind.LUNAR, LUNAR_TYPES)}
Search = Callable[[Ephemeris, AstronomicalTime, EclipseKind, frozenset[EclipseType]], Eclipse]
SIDES: tuple[tuple[str, str, Search], ...] = (
    ("before", "before the birth", previous_eclipse),
    ("after", "after the birth", next_eclipse),
)


@dataclass(frozen=True)
class EclipseResult:
    name: str  # e.g. "solar-before"
    title: str
    eclipse: Eclipse | None
    refusal: Refusal | None
    days: float | None  # greatest eclipse minus the birth, TT days
    degree: BodyPosition | None  # at the syzygy: the Sun (solar) or the Moon (lunar)


@login_required
def record_eclipses(request: HttpRequest, pk: int) -> HttpResponse:
    record = get_object_or_404(Record, pk=pk)
    context = {
        "record": record,
        **_form_values(request),
        "solar": request.GET.get("solar", ""),
        "lunar": request.GET.get("lunar", ""),
        "solar_types": sorted(t.value for t in SOLAR_TYPES),
        "lunar_types": sorted(t.value for t in LUNAR_TYPES),
    }
    try:
        scale = _time_scale(request)
        chosen = _chosen_types(request)
    except InvalidRequest as error:
        return _refused_page(request, "research_platform/eclipses.html", context, error)
    context["chosen"] = bool(chosen)
    if chosen:
        try:
            birth_record = record.birth_record()
            birth = _ephemeris().time(resolve(birth_record.time, birth_record.place), scale)
        except REFUSALS as refusal:
            context["refusal"] = Refusal(type(refusal).__name__, str(refusal))
        else:
            context["results"] = _results(birth, chosen)
    return render(request, "research_platform/eclipses.html", context)


def _time_scale(request: HttpRequest) -> TimeScale:
    name = request.GET.get("time_scale", DEFAULT_TIME_SCALE)
    if name not in TIME_SCALES:
        raise InvalidRequest(f"time scale {name!r} is not one of {', '.join(sorted(TIME_SCALES))}")
    return TIME_SCALES[name]


def _chosen_types(request: HttpRequest) -> dict[str, tuple[EclipseKind, frozenset[EclipseType]]]:
    """The kinds whose types are named, in order (solar, then lunar), with those types."""
    chosen = {}
    for name, (kind, allowed) in KINDS.items():
        text = request.GET.get(name, "").strip()
        if not text:
            continue
        if text == "all":
            chosen[name] = (kind, allowed)
            continue
        values = [value.strip() for value in text.split(",")]
        types = []
        for value in values:
            try:
                types.append(EclipseType(value))
            except ValueError:
                raise InvalidRequest(f"{name}: {value!r} is not an eclipse type") from None
        if len(set(types)) != len(types):
            raise InvalidRequest(f"{name}: eclipse types must not repeat")
        # The engine refuses the same; checked here first, so a refused record can't hide it.
        if not set(types) <= allowed:
            raise InvalidRequest(
                f"{kind.value} eclipse types must be a non-empty choice from "
                f"{sorted(t.value for t in allowed)}, not {sorted(t.value for t in types)}"
            )
        chosen[name] = (kind, frozenset(types))
    return chosen


def _results(
    birth: AstronomicalTime, chosen: dict[str, tuple[EclipseKind, frozenset[EclipseType]]]
) -> list[EclipseResult]:
    results = []
    for name, (kind, types) in chosen.items():
        for side, where, search in SIDES:
            title = f"The {kind.value} eclipse {where}"
            try:
                eclipse = search(_ephemeris(), birth, kind, types)
            except REFUSALS as refusal:
                refused = Refusal(type(refusal).__name__, str(refusal))
                results.append(EclipseResult(f"{name}-{side}", title, None, refused, None, None))
                continue
            at_syzygy = eclipse.syzygy
            degree = at_syzygy.sun if kind is EclipseKind.SOLAR else at_syzygy.moon
            days = eclipse.greatest.time.jd_tt - birth.jd_tt
            results.append(EclipseResult(f"{name}-{side}", title, eclipse, None, days, degree))
    return results
