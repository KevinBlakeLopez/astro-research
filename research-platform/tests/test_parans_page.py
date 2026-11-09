"""A record's parans: every rise, set and transit within birth +-12 hours, and the gaps.

The window is the project's convention, birth +-12 hours of clock time, and fixed. The bodies,
stars and horizon convention are named in the address and never defaulted:
- `bodies`: `all` or a comma-separated list of body names;
- `stars`: catalogue designations without their leading comma (`alLeo,alTau`), at most 100;
- `horizon`: `geometric` (disc centre, no refraction) or `almanac` (upper limb, 1013.25 hPa and
  10 C), or else `disc` with an optional `pressure` and `temperature` together.

The events are listed in time order. The gaps, every pair of events of two different points,
are listed closest first, the first 100 unless more are asked for. No orb is applied. Every gap
downloads as CSV, and the catalogue can be searched by name.

Markup the tests rely on: event rows carry data-event="<point>|<event>", gap rows
data-gap="<point>|<event>|<point>|<event>", the points that don't rise or set
data-unrisen="<point>|<event>", and search results data-star="<designation without comma>".
A point is a body's name or a star's designation (",alLeo").
"""

import csv
import html
import io
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from astro_research.astronomical_time import AstronomicalTime, TimeScale
from astro_research.chart import BirthRecord
from astro_research.civil_time import UtcTime, ZonedLocalTime, resolve
from astro_research.ephemeris import Ephemeris
from astro_research.geo import GeoPosition
from astro_research.parans import (
    AngularEvent,
    AngularEvents,
    Disc,
    FixedStar,
    HorizonConvention,
    ParanGap,
    Refraction,
    angular_events,
    paran_gaps,
)
from astro_research.positions import Body
from research_platform.models import Record

LONDON = GeoPosition(latitude=51.5074, longitude=-0.1278)
GOOD = BirthRecord(
    time=ZonedLocalTime(local=datetime(1987, 6, 15, 4, 25), zone="Europe/London"), place=LONDON
)
STARS = (FixedStar(designation=",alLeo"), FixedStar(designation=",alTau"))
GEOMETRIC = HorizonConvention(disc=Disc.CENTRE, refraction=None)
ALMANAC = HorizonConvention(
    disc=Disc.UPPER_LIMB, refraction=Refraction(pressure_hpa=1013.25, temperature_c=10.0)
)
NAMED = {"bodies": "all", "stars": "alLeo,alTau", "horizon": "geometric"}
NAMES = ("record-parans", "record-parans-events", "record-parans-gaps", "record-parans-csv")


def url(name: str, record: Record, /, **params: str) -> str:
    query = f"?{urlencode(params)}" if params else ""
    return reverse(name, args=[record.pk]) + query


def page(client: Client, address: str, **headers: str) -> str:
    response = client.get(address, headers=headers)
    assert response.status_code == 200, response.content.decode()[:500]
    return html.unescape(response.content.decode())


def at(eph: Ephemeris, utc: datetime) -> AstronomicalTime:
    return eph.time(resolve(UtcTime(utc=utc), LONDON), TimeScale.UT1_FROM_IERS)


def engine(
    eph: Ephemeris,
    horizon: HorizonConvention = GEOMETRIC,
    bodies: tuple[Body, ...] = tuple(Body),
    stars: tuple[FixedStar, ...] = STARS,
) -> AngularEvents:
    birth = resolve(GOOD.time, GOOD.place).utc
    start, end = at(eph, birth - timedelta(hours=12)), at(eph, birth + timedelta(hours=12))
    return angular_events(
        eph, LONDON, start=start, end=end, bodies=bodies, stars=stars, horizon=horizon
    )


def point(event: AngularEvent) -> str:
    return event.point.designation if isinstance(event.point, FixedStar) else event.point.value


def event_id(event: AngularEvent) -> str:
    return f"{point(event)}|{event.event.value}"


def gap_id(gap: ParanGap) -> str:
    return f"{event_id(gap.first)}|{event_id(gap.second)}"


def closest_first(gaps: tuple[ParanGap, ...]) -> list[ParanGap]:
    return sorted(gaps, key=lambda gap: abs(gap.seconds))


def shown(text: str, attribute: str) -> list[str]:
    return re.findall(rf'{attribute}="([^"]+)"', text)


# --- Access and what must be named ----------------------------------------------------------


@pytest.mark.django_db
def test_parans_need_a_login(client: Client, store: Callable[..., Record]) -> None:
    record = store(GOOD)
    for name in (*NAMES, "record-parans-stars"):
        response = client.get(url(name, record, **NAMED))
        assert response.status_code == 302 and "login" in response["Location"], name


@pytest.mark.django_db
@pytest.mark.parametrize(
    "params",
    [{}, {"horizon": "geometric"}, {"bodies": "all", "stars": "alLeo"}],
    ids=["nothing", "no points", "no horizon"],
)
def test_without_points_and_a_horizon_the_page_asks_for_them(
    admin_client: Client, store: Callable[..., Record], params: dict[str, str]
) -> None:
    text = page(admin_client, url("record-parans", store(GOOD), **params))
    for name in ("bodies", "stars", "horizon"):
        assert f'name="{name}"' in text
    assert shown(text, "data-event") == []  # nothing is ever chosen for you


# --- The events -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_page_lists_the_engine_s_events_in_time_order(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record]
) -> None:
    events = engine(eph)
    text = page(admin_client, url("record-parans", store(GOOD), **NAMED))
    assert events.start.instant.utc.isoformat() in text
    assert events.end.instant.utc.isoformat() in text
    assert shown(text, "data-event") == [event_id(event) for event in events.events]
    for event in events.events:
        assert event.time.instant.utc.isoformat() in text and f"{event.armc:.6f}" in text
    assert shown(text, "data-unrisen") == [
        f"{p.designation if isinstance(p, FixedStar) else p.value}|{e.value}"
        for p, e in events.no_rise_or_set
    ]
    for _star, name in events.star_names:
        assert name in text  # e.g. "Regulus,alLeo"


@pytest.mark.django_db
def test_the_events_can_be_narrowed_to_one_point(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record]
) -> None:
    address = url("record-parans-events", store(GOOD), **NAMED, involving="moon")
    text = page(admin_client, address, HX_Request="true")
    assert "<html" not in text
    moon = [event_id(event) for event in engine(eph).events if event.point is Body.MOON]
    assert moon and shown(text, "data-event") == moon


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("params", "horizon"),
    [
        ({"horizon": "almanac"}, ALMANAC),
        ({"disc": "upper_limb", "pressure": "1013.25", "temperature": "10"}, ALMANAC),
        ({"disc": "lower_limb"}, HorizonConvention(disc=Disc.LOWER_LIMB, refraction=None)),
    ],
    ids=["almanac", "the same named explicitly", "lower limb, no refraction"],
)
def test_the_horizon_convention_comes_from_the_address(
    admin_client: Client,
    eph: Ephemeris,
    store: Callable[..., Record],
    params: dict[str, str],
    horizon: HorizonConvention,
) -> None:
    address = url("record-parans", store(GOOD), bodies="sun,moon", stars="alLeo", **params)
    text = page(admin_client, address)
    events = engine(eph, horizon, bodies=(Body.SUN, Body.MOON), stars=STARS[:1])
    for event in events.events:
        assert event.time.instant.utc.isoformat() in text


# --- The gaps -------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_gaps_are_listed_closest_first(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record]
) -> None:
    gaps = closest_first(paran_gaps(engine(eph)))
    text = page(admin_client, url("record-parans", store(GOOD), **NAMED))
    assert shown(text, "data-gap") == [gap_id(gap) for gap in gaps[:100]]
    for gap in gaps[:5]:
        assert f"{gap.seconds:.3f}" in text and f"{gap.armc:.6f}" in text


@pytest.mark.django_db
def test_every_gap_can_be_shown(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record]
) -> None:
    address = url("record-parans-gaps", store(GOOD), **NAMED, limit="all")
    text = page(admin_client, address, HX_Request="true")
    assert shown(text, "data-gap") == [
        gap_id(gap) for gap in closest_first(paran_gaps(engine(eph)))
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["star-body", "star-star", "body-body"])
def test_the_gaps_can_be_narrowed_by_kind(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record], kind: str
) -> None:
    address = url("record-parans-gaps", store(GOOD), **NAMED, kind=kind, limit="all")
    text = page(admin_client, address, HX_Request="true")
    stars = {"star-body": 1, "star-star": 2, "body-body": 0}[kind]

    def star_count(gap: ParanGap) -> int:
        return sum(isinstance(e.point, FixedStar) for e in (gap.first, gap.second))

    expected = [g for g in closest_first(paran_gaps(engine(eph))) if star_count(g) == stars]
    assert expected and shown(text, "data-gap") == [gap_id(gap) for gap in expected]


@pytest.mark.django_db
def test_the_gaps_can_be_narrowed_to_one_star(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record]
) -> None:
    address = url("record-parans-gaps", store(GOOD), **NAMED, involving="alLeo", limit="all")
    text = page(admin_client, address, HX_Request="true")
    expected = [
        g
        for g in closest_first(paran_gaps(engine(eph)))
        if ",alLeo" in (point(g.first), point(g.second))
    ]
    assert expected and shown(text, "data-gap") == [gap_id(gap) for gap in expected]


@pytest.mark.django_db
def test_every_gap_downloads_as_csv(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    response = admin_client.get(url("record-parans-csv", record, **NAMED))
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert f'filename="parans-record-{record.pk}.csv"' in response["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(response.content.decode())))
    assert rows[0] == [
        "first_point",
        "first_event",
        "first_utc",
        "second_point",
        "second_event",
        "second_utc",
        "seconds",
        "armc",
    ]
    gaps = paran_gaps(engine(eph))
    assert len(rows) - 1 == len(gaps)
    for written, gap in zip(rows[1:], gaps, strict=True):
        assert written == [
            point(gap.first),
            gap.first.event.value,
            gap.first.time.instant.utc.isoformat(),
            point(gap.second),
            gap.second.event.value,
            gap.second.time.instant.utc.isoformat(),
            repr(gap.seconds),
            repr(gap.armc),
        ]


# --- Finding stars --------------------------------------------------------------------------


@pytest.mark.django_db
def test_stars_are_found_by_name_and_added_with_a_link(
    admin_client: Client, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    params = {"bodies": "all", "stars": "alTau", "horizon": "geometric", "q": "regul"}
    text = page(admin_client, url("record-parans-stars", record, **params), HX_Request="true")
    assert "<html" not in text
    assert shown(text, "data-star") == ["alLeo"]
    assert "Regulus" in text and "1.40" in text  # its magnitude
    added = urlencode({"bodies": "all", "stars": "alTau,alLeo", "horizon": "geometric"})
    assert f'href="{reverse("record-parans", args=[record.pk])}?{added}"' in text


@pytest.mark.django_db
def test_a_star_search_needs_two_letters(
    admin_client: Client, store: Callable[..., Record]
) -> None:
    text = page(admin_client, url("record-parans-stars", store(GOOD), q="r"), HX_Request="true")
    assert shown(text, "data-star") == []


# --- Refusals -------------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("params", "reason"),
    [
        ({**NAMED, "stars": "alZzz"}, "alZzz"),
        # Swiss Ephemeris's lookup finds zePscB, which the catalogue doesn't have (observed).
        ({**NAMED, "stars": "zePscB"}, "'zePscB' is not in the star catalogue"),
        ({**NAMED, "bodies": "sun,chiron"}, "'chiron' is not a body"),
        ({**NAMED, "stars": ",".join(["alLeo"] * 2)}, "must not repeat"),
        ({**NAMED, "horizon": "sideways"}, "'sideways' is not"),
        ({"bodies": "all", "disc": "centre", "pressure": "-5", "temperature": "10"}, "pressure"),
        ({"bodies": "all", "disc": "centre", "pressure": "1013.25"}, "both a pressure and a"),
        ({**NAMED, "disc": "centre"}, "either a horizon or a disc"),
    ],
    ids=[
        "unknown star",
        "a star Swiss Ephemeris finds but the catalogue lacks",
        "unknown body",
        "a star twice",
        "unknown horizon",
        "negative pressure",
        "pressure without temperature",
        "horizon and disc",
    ],
)
def test_points_and_horizons_the_engine_can_t_use_are_refused_in_place(
    admin_client: Client, store: Callable[..., Record], params: dict[str, str], reason: str
) -> None:
    response = admin_client.get(url("record-parans", store(GOOD), **params))
    assert response.status_code == 400
    text = html.unescape(response.content.decode())
    box = re.search(r'data-error="request"[^>]*>(.*?)</div>', text, re.S)
    assert box is not None and reason in box.group(1)
    assert shown(text, "data-event") == []


@pytest.mark.django_db
def test_at_most_100_stars(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record]
) -> None:
    designations = list(
        dict.fromkeys(entry.star.designation.lstrip(",") for entry in eph.star_catalogue())
    )
    record = store(GOOD)
    hundred = {**NAMED, "stars": ",".join(designations[:100])}
    assert admin_client.get(url("record-parans-csv", record, **hundred)).status_code == 200
    more = {**NAMED, "stars": ",".join(designations[:101])}
    assert admin_client.get(url("record-parans-csv", record, **more)).status_code == 400


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("birth_record", "refusal"),
    [
        (
            BirthRecord(
                time=UtcTime(utc=datetime(2000, 1, 1, tzinfo=UTC)),
                place=GeoPosition(latitude=90.0, longitude=0.0),
            ),
            "AnglesUndefined",
        ),
        (
            BirthRecord(time=UtcTime(utc=datetime(2026, 10, 9, 18, tzinfo=UTC)), place=LONDON),
            "DateOutsideUt1Data",
        ),
        (
            BirthRecord(
                time=ZonedLocalTime(local=datetime(1987, 10, 25, 1, 30), zone="Europe/London"),
                place=LONDON,
            ),
            "AmbiguousLocalTime",
        ),
    ],
    ids=["the pole", "the window past IERS's last day", "an ambiguous local time"],
)
def test_a_window_the_engine_refuses_shows_its_refusal(
    admin_client: Client, store: Callable[..., Record], birth_record: BirthRecord, refusal: str
) -> None:
    record = store(birth_record)
    text = page(admin_client, url("record-parans", record, **NAMED))
    assert refusal in text and shown(text, "data-event") == []
    assert admin_client.get(url("record-parans-csv", record, **NAMED)).status_code == 422


@pytest.mark.django_db
def test_an_unknown_record_is_not_found(admin_client: Client) -> None:
    for name in NAMES:
        address = reverse(name, args=[999_999]) + f"?{urlencode(NAMED)}"
        assert admin_client.get(address).status_code == 404, name


# --- Getting there --------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_chart_page_and_the_record_s_admin_page_link_to_it(
    admin_client: Client, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    path = reverse("record-parans", args=[record.pk])
    chart = page(admin_client, url("record-chart", record, time_scale="clock-as-ut1"))
    assert re.search(rf'href="{re.escape(path)}\?[^"]*time_scale=clock-as-ut1', chart)
    change = page(admin_client, f"/admin/research_platform/record/{record.pk}/change/")
    assert f'href="{path}"' in change


@pytest.mark.django_db
def test_the_page_drives_its_fragments_with_htmx(
    admin_client: Client, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    text = page(admin_client, url("record-parans", record, **NAMED))
    for name in ("record-parans-events", "record-parans-gaps", "record-parans-stars"):
        assert f'hx-get="{reverse(name, args=[record.pk])}"' in text, name
