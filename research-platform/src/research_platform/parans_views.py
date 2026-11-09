"""A record's parans: every rise, set and transit within birth +-12 hours, and the gaps.

The window is the project's convention: the birth's UTC instant +-12 hours of clock time, fixed.
The bodies, stars and horizon convention come from the address and are never defaulted. Stars
are named by catalogue designation without the leading comma (`alLeo,alTau`, at most 100) and
checked against the verified catalogue exactly. Swiss Ephemeris's own lookup also finds zePscB,
which the file doesn't have (observed), so it can't be the check.

The events (the engine's `angular_events`) are listed in time order; the gaps (`paran_gaps`),
every pair of events of two different points, closest first, the first 100 unless `limit` says
otherwise. No orb is applied: how close counts is the researcher's rule. 50 stars and the ten
bodies take about 30 ms and give 19,054 gaps (measured).
"""

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from astro_research.astronomical_time import AstronomicalTime, TimeScale
from astro_research.civil_time import UtcTime, resolve
from astro_research.geo import GeoPosition
from astro_research.parans import (
    AngularEvent,
    AngularEvents,
    Disc,
    FixedStar,
    HorizonConvention,
    ParanGap,
    Refraction,
    StarNotFound,
    angular_events,
    paran_gaps,
)
from astro_research.positions import Body
from research_platform.eclipses_views import _time_scale
from research_platform.invalid_requests import InvalidRequest, download, fragment
from research_platform.models import Record
from research_platform.runner import REFUSALS
from research_platform.views import Refusal, _ephemeris, _form_values, _refused_page

WINDOW = timedelta(hours=12)
MAX_STARS = 100
LIST_LIMIT = 100
SEARCH_RESULTS = 20
KINDS = {"star-body": 1, "star-star": 2, "body-body": 0}  # how many of the two points are stars
HORIZONS = {
    "geometric": HorizonConvention(disc=Disc.CENTRE, refraction=None),
    "almanac": HorizonConvention(
        disc=Disc.UPPER_LIMB, refraction=Refraction(pressure_hpa=1013.25, temperature_c=10.0)
    ),
}
FORM_FIELDS = ("bodies", "stars", "horizon", "disc", "pressure", "temperature", "time_scale")
CSV_COLUMNS = (
    "first_point",
    "first_event",
    "first_utc",
    "second_point",
    "second_event",
    "second_utc",
    "seconds",
    "armc",
)


@dataclass(frozen=True)
class Request:
    """What the address names: points and a horizon, or None where it names none."""

    bodies: tuple[Body, ...]
    stars: tuple[FixedStar, ...]
    horizon: HorizonConvention | None
    time_scale: TimeScale

    @property
    def complete(self) -> bool:
        return bool(self.bodies or self.stars) and self.horizon is not None


@dataclass(frozen=True)
class GapFilter:
    involving: str  # a body's name, a star's designation without its comma, or ""
    kind: str
    limit: int | None


@login_required
def record_parans(request: HttpRequest, pk: int) -> HttpResponse:
    record = get_object_or_404(Record, pk=pk)
    context: dict[str, object] = {
        "record": record,
        **_form_values(request),
        **{name: request.GET.get(name, "") for name in FORM_FIELDS if name != "time_scale"},
        "discs": [disc.value for disc in Disc],
        "kinds": list(KINDS),
    }
    try:
        named = _named(request)
        context["complete"] = named.complete
        if named.complete:
            context |= _results(request, record, named)
    except InvalidRequest as error:
        return _refused_page(request, "research_platform/parans.html", context, error)
    return render(request, "research_platform/parans.html", context)


def _results(request: HttpRequest, record: Record, named: Request) -> dict[str, object]:
    """The page's results: the events and gaps, or the engine's refusal."""
    events, refusal = _events(record, named)
    gap_filter = _gap_filter(request, named)
    event_filter = _involving(request, named)
    results: dict[str, object] = {
        "refusal": refusal,
        "filter": gap_filter,
        "event_filter": event_filter,
        "points": _point_names(named),
    }
    if events is not None:
        results |= {
            "events": events,
            "event_rows": _event_rows(events, event_filter),
            "unrisen": [f"{_name(p)}|{e.value}" for p, e in events.no_rise_or_set],
            "gaps": _gap_rows(events, gap_filter),
            "gap_count": len(paran_gaps(events)),
        }
    return results


@login_required
@fragment
def record_parans_events(request: HttpRequest, pk: int) -> HttpResponse:
    record = get_object_or_404(Record, pk=pk)
    named = _required(request)
    events, refusal = _events(record, named)
    if events is None:
        return render(request, "research_platform/refusal.html", {"refusal": refusal}, status=422)
    rows = _event_rows(events, _involving(request, named))
    return render(request, "research_platform/parans_events.html", {"event_rows": rows})


@login_required
@fragment
def record_parans_gaps(request: HttpRequest, pk: int) -> HttpResponse:
    record = get_object_or_404(Record, pk=pk)
    named = _required(request)
    events, refusal = _events(record, named)
    if events is None:
        return render(request, "research_platform/refusal.html", {"refusal": refusal}, status=422)
    gap_filter = _gap_filter(request, named)
    context = {"gaps": _gap_rows(events, gap_filter), "gap_count": len(paran_gaps(events))}
    return render(request, "research_platform/parans_gaps.html", context)


@login_required
@download
def record_parans_csv(request: HttpRequest, pk: int) -> HttpResponse:
    record = get_object_or_404(Record, pk=pk)
    events, refusal = _events(record, _required(request))
    if events is None:
        assert refusal is not None
        text = f"{refusal.name}: {refusal.message}\n"
        return HttpResponse(text, status=422, content_type="text/plain")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for gap in paran_gaps(events):
        writer.writerow(
            [
                _name(gap.first.point),
                gap.first.event.value,
                gap.first.time.instant.utc.isoformat(),
                _name(gap.second.point),
                gap.second.event.value,
                gap.second.time.instant.utc.isoformat(),
                repr(gap.seconds),  # Python's shortest repr reads back exactly
                repr(gap.armc),
            ]
        )
    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="parans-record-{record.pk}.csv"'
    return response


@login_required
@fragment
def record_parans_stars(request: HttpRequest, pk: int) -> HttpResponse:
    """Catalogue stars whose name or designation holds the search, each a link adding it."""
    record = get_object_or_404(Record, pk=pk)
    query = request.GET.get("q", "").strip().lower()
    found = []
    if len(query) >= 2:
        for entry in _ephemeris().star_catalogue():
            designation = entry.star.designation.lstrip(",")
            if query in entry.name.lower() or query in designation.lower():
                found.append((entry, designation, _adding(request, record, designation)))
            if len(found) == SEARCH_RESULTS:
                break
    return render(request, "research_platform/parans_stars.html", {"found": found})


def _named(request: HttpRequest) -> Request:
    return Request(
        bodies=_bodies(request.GET.get("bodies", "")),
        stars=_stars(request.GET.get("stars", "")),
        horizon=_horizon(request),
        time_scale=_time_scale(request),
    )


def _required(request: HttpRequest) -> Request:
    named = _named(request)
    if not named.complete:
        raise InvalidRequest("name the bodies or stars, and a horizon convention")
    return named


def _bodies(text: str) -> tuple[Body, ...]:
    if text.strip() == "all":
        return tuple(Body)
    bodies = []
    for value in (part.strip() for part in text.split(",")):
        if not value:
            continue
        try:
            bodies.append(Body(value))
        except ValueError:
            raise InvalidRequest(f"bodies: {value!r} is not a body") from None
    if len(set(bodies)) != len(bodies):
        raise InvalidRequest("bodies must not repeat")
    return tuple(bodies)


def _stars(text: str) -> tuple[FixedStar, ...]:
    designations = [part.strip().lstrip(",") for part in text.split(",") if part.strip()]
    if len(set(designations)) != len(designations):
        raise InvalidRequest("stars must not repeat")
    if len(designations) > MAX_STARS:
        raise InvalidRequest(f"at most {MAX_STARS} stars")
    known = {entry.star.designation for entry in _ephemeris().star_catalogue()}
    stars = tuple(FixedStar(designation=f",{designation}") for designation in designations)
    for star in stars:
        if star.designation not in known:
            raise InvalidRequest(f"stars: {star.designation[1:]!r} is not in the star catalogue")
    return stars


def _horizon(request: HttpRequest) -> HorizonConvention | None:
    name, disc = request.GET.get("horizon", ""), request.GET.get("disc", "")
    pressure, temperature = request.GET.get("pressure", ""), request.GET.get("temperature", "")
    if name and disc:
        raise InvalidRequest("name either a horizon or a disc, not both")
    if name:
        if name not in HORIZONS:
            raise InvalidRequest(f"horizon {name!r} is not 'geometric' or 'almanac'")
        return HORIZONS[name]
    if not disc:
        return None
    try:
        chosen = Disc(disc)
    except ValueError:
        raise InvalidRequest(f"disc {disc!r} is not one of {[d.value for d in Disc]}") from None
    if bool(pressure) != bool(temperature):
        raise InvalidRequest("name both a pressure and a temperature, or neither")
    if not pressure:
        return HorizonConvention(disc=chosen, refraction=None)
    try:
        refraction = Refraction(pressure_hpa=float(pressure), temperature_c=float(temperature))
    except ValueError as error:  # the engine's refusal, or text that isn't a number
        raise InvalidRequest(f"refraction: {error}") from error
    return HorizonConvention(disc=chosen, refraction=refraction)


def _events(record: Record, named: Request) -> tuple[AngularEvents | None, Refusal | None]:
    """The window's events, or the engine's refusal of the record or the window."""
    assert named.horizon is not None
    try:
        birth_record = record.birth_record()
        birth = resolve(birth_record.time, birth_record.place).utc
        start, end = (
            _at(birth + offset, birth_record.place, named.time_scale)
            for offset in (-WINDOW, WINDOW)
        )
        events = angular_events(
            _ephemeris(),
            birth_record.place,
            start=start,
            end=end,
            bodies=named.bodies,
            stars=named.stars,
            horizon=named.horizon,
        )
    except StarNotFound as error:  # checked against the catalogue already; never guessed around
        raise InvalidRequest(str(error)) from error
    except REFUSALS as refusal:
        return None, Refusal(type(refusal).__name__, str(refusal))
    return events, None


def _at(utc: datetime, place: GeoPosition, scale: TimeScale) -> AstronomicalTime:
    return _ephemeris().time(resolve(UtcTime(utc=utc), place), scale)


def _point_names(named: Request) -> list[str]:
    return [body.value for body in named.bodies] + [s.designation[1:] for s in named.stars]


def _involving(request: HttpRequest, named: Request) -> str:
    involving = request.GET.get("involving", "")
    if involving and involving not in _point_names(named):
        raise InvalidRequest(f"involving {involving!r} is not one of the points named")
    return involving


def _gap_filter(request: HttpRequest, named: Request) -> GapFilter:
    kind = request.GET.get("kind", "")
    if kind and kind not in KINDS:
        raise InvalidRequest(f"kind {kind!r} is not one of {', '.join(KINDS)}")
    limit_text = request.GET.get("limit", "")
    limit: int | None = LIST_LIMIT
    if limit_text == "all":
        limit = None
    elif limit_text:
        if not limit_text.isdigit() or int(limit_text) == 0:
            raise InvalidRequest(f"limit {limit_text!r} is neither 'all' nor a positive number")
        limit = int(limit_text)
    return GapFilter(involving=_involving(request, named), kind=kind, limit=limit)


def _name(point: Body | FixedStar) -> str:
    """A point as the markup and CSV write it: a body's name, or a star's designation."""
    return point.designation if isinstance(point, FixedStar) else point.value


def _short(point: Body | FixedStar) -> str:
    """A point as the address names it: a body's name, or a designation without its comma."""
    return point.designation[1:] if isinstance(point, FixedStar) else point.value


def _event_rows(events: AngularEvents, involving: str) -> list[tuple[str, AngularEvent]]:
    return [
        (f"{_name(event.point)}|{event.event.value}", event)
        for event in events.events
        if not involving or _short(event.point) == involving
    ]


def _gap_rows(events: AngularEvents, gap_filter: GapFilter) -> list[tuple[str, ParanGap]]:
    def stars(gap: ParanGap) -> int:
        return sum(isinstance(event.point, FixedStar) for event in (gap.first, gap.second))

    chosen = [
        gap
        for gap in sorted(paran_gaps(events), key=lambda gap: abs(gap.seconds))
        if (not gap_filter.kind or stars(gap) == KINDS[gap_filter.kind])
        and (
            not gap_filter.involving
            or gap_filter.involving in (_short(gap.first.point), _short(gap.second.point))
        )
    ]
    if gap_filter.limit is not None:
        chosen = chosen[: gap_filter.limit]
    return [
        (
            f"{_name(g.first.point)}|{g.first.event.value}|"
            f"{_name(g.second.point)}|{g.second.event.value}",
            g,
        )
        for g in chosen
    ]


def _adding(request: HttpRequest, record: Record, designation: str) -> str:
    """The page's address with `designation` added to its stars, everything else kept."""
    params = [(name, value) for name, value in request.GET.items() if name != "q"]
    stars = request.GET.get("stars", "")
    added = f"{stars},{designation}" if stars else designation
    if any(name == "stars" for name, _ in params):
        params = [(n, added if n == "stars" else v) for n, v in params]
    else:
        params.append(("stars", added))
    return f"{reverse('record-parans', args=[record.pk])}?{urlencode(params)}"
