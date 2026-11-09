"""The web interface: logging in, the admin, and a record's chart page.

The chart page computes the chart on request under the settings in its address, so the address
reproduces what the page shows. Aspect angles are never defaulted: without them the page shows
everything but the deviations. The pairs table is filtered and sorted by an HTMX fragment, and
the chart downloads as its chart-result-v1 document.

Markup the tests rely on: each pair row carries data-pair="a-b", and each deviation cell
data-angle="<angle>".
"""

import hashlib
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

import research_platform
from astro_research.astronomical_time import TimeScale
from astro_research.chart import BirthRecord, Chart, ChartSettings, compute_chart
from astro_research.civil_time import ZonedLocalTime
from astro_research.ephemeris import Ephemeris
from astro_research.geo import GeoPosition
from astro_research.measurements import MAJOR
from astro_research.result_format import chart_to_json
from research_platform.models import Record, Source, Subject

LONDON = GeoPosition(latitude=51.5074, longitude=-0.1278)
GOOD = BirthRecord(
    time=ZonedLocalTime(local=datetime(1987, 6, 15, 4, 25), zone="Europe/London"), place=LONDON
)
AMBIGUOUS = BirthRecord(
    time=ZonedLocalTime(local=datetime(1987, 10, 25, 1, 30), zone="Europe/London"), place=LONDON
)
HTMX = Path(research_platform.__file__).parent / "static/research_platform/htmx-2.0.11.min.js"
HTMX_SHA256 = "d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717"


def url(name: str, record: Record, **params: str) -> str:
    query = f"?{urlencode(params)}" if params else ""
    return reverse(name, args=[record.pk]) + query


def page(client: Client, address: str, **headers: str) -> str:
    response = client.get(address, headers=headers)
    assert response.status_code == 200, response.content.decode()[:500]
    text: str = response.content.decode()
    return text


def engine_chart(eph: Ephemeris, settings: ChartSettings) -> Chart:
    return compute_chart(eph, GOOD, settings)


# --- Logging in -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_every_page_needs_a_login(client: Client, store: Callable[..., Record]) -> None:
    record = store(GOOD)
    for address in (
        "/admin/",
        url("record-chart", record),
        url("record-chart-pairs", record),
        url("record-chart-json", record),
    ):
        response = client.get(address)
        assert response.status_code == 302, address
        assert "login" in response["Location"], address


@pytest.mark.django_db
def test_the_login_page_asks_for_a_username_and_password(client: Client) -> None:
    html = page(client, reverse("login"))
    assert 'name="username"' in html and 'name="password"' in html


# --- The admin ------------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("model", ["subject", "source", "record", "dataset"])
def test_the_admin_lists_and_adds_the_data(admin_client: Client, model: str) -> None:
    page(admin_client, f"/admin/research_platform/{model}/")
    page(admin_client, f"/admin/research_platform/{model}/add/")


@pytest.mark.django_db
@pytest.mark.parametrize("model", ["calculationrun", "runoutcome", "storedchart"])
def test_runs_outcomes_and_stored_charts_are_read_only_in_the_admin(
    admin_client: Client, model: str
) -> None:
    page(admin_client, f"/admin/research_platform/{model}/")
    assert admin_client.get(f"/admin/research_platform/{model}/add/").status_code == 403


@pytest.mark.django_db
def test_the_admin_refuses_a_record_the_engine_refuses(
    admin_client: Client, subject: Subject, source: Source
) -> None:
    fields: dict[str, Any] = {
        "subject": subject.pk,
        "source": source.pk,
        "time_kind": "zoned_local_time",
        "reading": "1987-06-15T04:25:00",
        "zone": "Europe/London",
        "utc_offset_us": "",
        "latitude": "95",
        "longitude": "-0.1278",
        "elevation_m": "0",
        "time_resolution": "",
        "stated_uncertainty_us": "",
    }
    response = admin_client.post("/admin/research_platform/record/add/", fields)
    assert response.status_code == 200  # the form again, with the engine's reason
    assert "latitude 95.0 is not within [-90, 90]" in response.content.decode()
    assert not Record.objects.exists()


@pytest.mark.django_db
def test_a_record_s_admin_page_links_to_its_chart(
    admin_client: Client, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    html = page(admin_client, f"/admin/research_platform/record/{record.pk}/change/")
    assert f'href="{url("record-chart", record)}"' in html


# --- The chart page -------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_chart_page_shows_the_engine_s_chart(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    chart = engine_chart(eph, ChartSettings(aspect_angles=MAJOR))
    html = page(admin_client, url("record-chart", record, aspects="major"))
    assert chart.calculation_key in html
    assert chart.time.instant.utc.isoformat() in html
    assert chart.time.ut1_basis.value in html
    for body in chart.positions.bodies:
        assert f"{body.longitude:.6f}" in html and f"{body.declination:.6f}" in html
    for name in ("ascendant", "midheaven", "vertex", "armc"):
        assert f"{getattr(chart.angles, name):.6f}" in html
    assert len(re.findall(r'data-pair="', html)) == len(chart.pairs)
    for deviation in chart.pairs[0].deviations:
        assert f'data-angle="{deviation.angle}"' in html
        assert f"{deviation.deviation:.6f}" in html


@pytest.mark.django_db
def test_without_aspect_angles_the_page_shows_no_deviations_and_asks_for_them(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    chart = engine_chart(eph, ChartSettings(aspect_angles=()))
    html = page(admin_client, url("record-chart", record))
    assert chart.calculation_key in html
    assert len(re.findall(r'data-pair="', html)) == len(chart.pairs)
    assert f"{chart.pairs[0].separation:.6f}" in html
    assert "data-angle=" not in html
    assert 'name="aspects"' in html  # the page asks for them; it never picks any


@pytest.mark.django_db
def test_the_settings_come_from_the_address(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    settings = ChartSettings(aspect_angles=(45.0, 135.0), time_scale=TimeScale.CLOCK_AS_UT1)
    chart = engine_chart(eph, settings)
    html = page(
        admin_client, url("record-chart", record, aspects="45,135", time_scale="clock-as-ut1")
    )
    assert chart.calculation_key in html
    assert "clock_reading" in html
    assert 'data-angle="45.0"' in html and 'data-angle="135.0"' in html
    assert 'data-angle="90.0"' not in html


@pytest.mark.django_db
@pytest.mark.parametrize(
    "params",
    [
        {"aspects": "major,banana"},
        {"aspects": "200"},
        {"aspects": "-10"},
        {"time_scale": "sidereal"},
    ],
    ids=["not an angle", "over 180", "negative", "unknown time scale"],
)
def test_settings_the_engine_can_t_use_are_refused(
    admin_client: Client, store: Callable[..., Record], params: dict[str, str]
) -> None:
    record = store(GOOD)
    for name in ("record-chart", "record-chart-pairs", "record-chart-json"):
        assert admin_client.get(url(name, record, **params)).status_code == 400, name


@pytest.mark.django_db
def test_a_refused_record_shows_the_engine_s_refusal_instead_of_a_chart(
    admin_client: Client, store: Callable[..., Record]
) -> None:
    record = store(AMBIGUOUS)
    html = page(admin_client, url("record-chart", record, aspects="major"))
    assert "AmbiguousLocalTime" in html
    assert "occurs twice in Europe/London" in html
    assert "data-pair=" not in html
    response = admin_client.get(url("record-chart-json", record, aspects="major"))
    assert response.status_code == 422
    assert "AmbiguousLocalTime" in response.content.decode()


@pytest.mark.django_db
def test_an_unknown_record_is_not_found(admin_client: Client) -> None:
    for name in ("record-chart", "record-chart-pairs", "record-chart-json"):
        assert admin_client.get(reverse(name, args=[999_999])).status_code == 404


# --- The pairs fragment ---------------------------------------------------------------------


@pytest.mark.django_db
def test_the_pairs_are_filtered_and_sorted_by_a_fragment(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    chart = engine_chart(eph, ChartSettings(aspect_angles=MAJOR))
    params = {"aspects": "major", "point": "sun", "angle": "90", "sort": "deviation"}
    html = page(admin_client, url("record-chart-pairs", record, **params), HX_Request="true")
    assert "<html" not in html  # only the table, for HTMX to swap in
    involving_sun = [p for p in chart.pairs if "sun" in (p.a.point.value, p.b.point.value)]
    by_deviation = sorted(
        involving_sun,
        key=lambda p: abs(next(d.deviation for d in p.deviations if d.angle == 90.0)),
    )
    shown = re.findall(r'data-pair="([a-z_]+)-([a-z_]+)"', html)
    assert shown == [(p.a.point.value, p.b.point.value) for p in by_deviation]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "params",
    [
        {"aspects": "major", "point": "chiron"},
        {"aspects": "major", "angle": "45"},
        {"aspects": "major", "sort": "brightness"},
        {"sort": "deviation"},
    ],
    ids=["unknown point", "an angle not named", "unknown order", "deviation with no angle"],
)
def test_a_filter_the_chart_can_t_apply_is_refused(
    admin_client: Client, store: Callable[..., Record], params: dict[str, str]
) -> None:
    record = store(GOOD)
    assert admin_client.get(url("record-chart-pairs", record, **params)).status_code == 400


@pytest.mark.django_db
def test_the_chart_page_drives_the_fragment_with_htmx(
    admin_client: Client, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    html = page(admin_client, url("record-chart", record, aspects="major"))
    assert f'hx-get="{reverse("record-chart-pairs", args=[record.pk])}"' in html
    scripts = re.findall(r'<script[^>]*src="([^"]+)"', html)
    assert scripts == ["/static/research_platform/htmx-2.0.11.min.js"]  # nothing from elsewhere


def test_htmx_is_the_pinned_file() -> None:
    # HTMX 2.0.11 (0BSD), from the npm package whose SHA-512 matched the registry's.
    assert hashlib.sha256(HTMX.read_bytes()).hexdigest() == HTMX_SHA256


# --- The download ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_chart_downloads_as_its_json_document(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    chart = engine_chart(eph, ChartSettings(aspect_angles=MAJOR))
    response = admin_client.get(url("record-chart-json", record, aspects="major"))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert response.content.decode() == chart_to_json(chart)
    assert f'filename="{chart.calculation_key}.json"' in response["Content-Disposition"]
