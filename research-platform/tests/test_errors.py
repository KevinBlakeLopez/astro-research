"""A refused request shows its reason where it happened, with debug mode on or off.

Django shows a bare "Bad Request (400)" for a BadRequest when debug mode is off, and a traceback
page when it's on (both observed). Instead, a page comes back with its forms as submitted and
the reason above them; a fragment comes back as just the reason, which the base page tells HTMX
to swap in (HTMX doesn't swap 4xx responses by default); a download comes back as the reason in
plain text. All with status 400.

Markup the tests rely on: the reason's box carries data-error="request".
"""

import html
import json
import re
from collections.abc import Callable
from datetime import datetime

import pytest
from django.test import Client
from pytest_django import Settings

from astro_research.chart import BirthRecord
from astro_research.civil_time import ZonedLocalTime
from astro_research.geo import GeoPosition
from research_platform.models import Record, Subject

LONDON = GeoPosition(latitude=51.5074, longitude=-0.1278)
FIRST = BirthRecord(
    time=ZonedLocalTime(local=datetime(1987, 6, 15, 4, 25), zone="Europe/London"), place=LONDON
)
SECOND = BirthRecord(
    time=ZonedLocalTime(local=datetime(1987, 6, 15, 4, 26), zone="Europe/London"), place=LONDON
)
DEBUG = pytest.mark.parametrize("debug", [False, True], ids=["debug off", "debug on"])


@pytest.fixture
def records(store: Callable[..., Record]) -> tuple[Record, Record]:
    return store(FIRST), store(SECOND, subject=Subject.objects.create(label="subject-2"))


def refused(client: Client, path: str, *, fragment: bool = False) -> tuple[str, str]:
    """The body and content type of a request that must be refused with 400."""
    headers = {"HX-Request": "true"} if fragment else {}
    response = client.get(path, headers=headers)
    assert response.status_code == 400, response.content.decode()[:300]
    body = html.unescape(response.content.decode())
    assert "Traceback" not in body and "Bad Request (400)" not in body
    return body, response["Content-Type"]


def page_paths(records: tuple[Record, Record]) -> dict[str, tuple[str, str, dict[str, str]]]:
    """Per page: the refused address, the reason, and the form values it must keep."""
    a, b = records
    return {
        "chart, bad aspects": (
            f"/records/{a.pk}/chart/?aspects=banana",
            "is neither an angle nor a set name",
            {"aspects": "banana"},
        ),
        "chart, bad filter": (
            f"/records/{a.pk}/chart/?aspects=major&point=chiron",
            "is not one of this chart's points",
            {"aspects": "major"},
        ),
        "comparison, bad time scale": (
            f"/records/{a.pk}/compare/{b.pk}/?aspects=45&time_scale=sidereal",
            "time scale 'sidereal' is not one of",
            {"aspects": "45"},
        ),
        "sensitivity, uneven offsets": (
            f"/records/{a.pk}/sensitivity/?aspects=major&span=600&step=70",
            "must be a whole number of steps",
            {"aspects": "major", "span": "600", "step": "70"},
        ),
    }


def fragment_paths(records: tuple[Record, Record]) -> dict[str, tuple[str, str]]:
    a, b = records
    return {
        "chart pairs": (
            f"/records/{a.pk}/chart/pairs/?aspects=major&point=chiron",
            "is not one of this chart's points",
        ),
        "comparison pairs": (
            f"/records/{a.pk}/compare/{b.pk}/pairs/?aspects=major&involving=chiron",
            "is neither 'angles' nor one of the points",
        ),
        "sensitivity series": (
            f"/records/{a.pk}/sensitivity/series/?span=600&step=60&quantity=brightness",
            "is not one the sweep measures",
        ),
        "sensitivity detail": (
            f"/records/{a.pk}/sensitivity/detail/?span=600&step=60&quantity=separation&a=sun",
            "the sweep has no such series",
        ),
    }


def download_paths(records: tuple[Record, Record]) -> dict[str, tuple[str, str]]:
    a, _ = records
    return {
        "chart JSON": (f"/records/{a.pk}/chart.json?aspects=banana", "neither an angle"),
        "sensitivity CSV": (f"/records/{a.pk}/sensitivity.csv?span=600", "name both a span"),
    }


@pytest.mark.django_db
@DEBUG
@pytest.mark.parametrize("case", list(page_paths((Record(pk=1), Record(pk=2)))))
def test_a_refused_page_shows_the_reason_above_its_forms_as_submitted(
    admin_client: Client,
    settings: Settings,
    records: tuple[Record, Record],
    debug: bool,
    case: str,
) -> None:
    settings.DEBUG = debug
    path, reason, kept = page_paths(records)[case]
    body, content_type = refused(admin_client, path)
    assert content_type.startswith("text/html")
    box = re.search(r'data-error="request"[^>]*>(.*?)</div>', body, re.S)
    assert box is not None and reason in box.group(1)
    for name, value in kept.items():
        assert re.search(rf'name="{name}"[^>]*value="{re.escape(value)}"', body), name
    assert "data-pair=" not in body and "data-series=" not in body  # no results


@pytest.mark.django_db
@DEBUG
@pytest.mark.parametrize("case", list(fragment_paths((Record(pk=1), Record(pk=2)))))
def test_a_refused_fragment_is_just_the_reason(
    admin_client: Client,
    settings: Settings,
    records: tuple[Record, Record],
    debug: bool,
    case: str,
) -> None:
    settings.DEBUG = debug
    path, reason = fragment_paths(records)[case]
    body, _ = refused(admin_client, path, fragment=True)
    assert "<html" not in body
    assert 'data-error="request"' in body and reason in body


@pytest.mark.django_db
@DEBUG
@pytest.mark.parametrize("case", list(download_paths((Record(pk=1), Record(pk=2)))))
def test_a_refused_download_is_the_reason_in_plain_text(
    admin_client: Client,
    settings: Settings,
    records: tuple[Record, Record],
    debug: bool,
    case: str,
) -> None:
    settings.DEBUG = debug
    path, reason = download_paths(records)[case]
    body, content_type = refused(admin_client, path)
    assert content_type.startswith("text/plain")
    assert reason in body and "<" not in body


@pytest.mark.django_db
def test_the_pages_tell_htmx_to_swap_in_refusals(
    admin_client: Client, records: tuple[Record, Record]
) -> None:
    # HTMX 2's default doesn't swap any 4xx response, so a refused fragment (400) or a refused
    # record's fragment (422) would leave the old table in place and show nothing.
    body = admin_client.get(f"/records/{records[0].pk}/chart/?aspects=major").content.decode()
    meta = re.search(r'<meta name="htmx-config" content=\'([^\']+)\'>', body)
    assert meta is not None
    handling = json.loads(meta.group(1))["responseHandling"]
    codes = [entry["code"] for entry in handling]
    assert {"code": "400", "swap": True} in handling and {"code": "422", "swap": True} in handling
    assert codes.index("400") < codes.index("[45]..") and codes.index("422") < codes.index("[45]..")
    assert {"code": "[45]..", "swap": False, "error": True} in handling  # the rest as before
