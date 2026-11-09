"""A record's nearest eclipses: the solar and lunar eclipse before and after the birth.

The types are named in the address, never defaulted: `solar` and `lunar`, each a comma-separated
list of types or `all`. A kind is shown only when its types are named. Each eclipse shows its
type, greatest eclipse and syzygy, its distance from the birth, its degree (at the syzygy: the
Sun's for a solar eclipse, the Moon's for a lunar one), magnitudes, saros, and for a solar
eclipse its central flag and place of greatest eclipse, with the Sun and Moon at both instants.

An eclipse the engine refuses shows its refusal in its own place; the others still show.

Markup the tests rely on: each eclipse's block carries data-eclipse="<kind>-<before|after>", and
its degree's cells data-degree="longitude" and data-degree="declination".
"""

import html
import re
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from astro_research.astronomical_time import AstronomicalTime, TimeScale
from astro_research.chart import BirthRecord
from astro_research.civil_time import UtcTime, ZonedLocalTime, resolve
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
from astro_research.geo import GeoPosition
from research_platform.models import Record

LONDON = GeoPosition(latitude=51.5074, longitude=-0.1278)
GOOD = BirthRecord(
    time=ZonedLocalTime(local=datetime(1987, 6, 15, 4, 25), zone="Europe/London"), place=LONDON
)
EVERY_TYPE = {"solar": "all", "lunar": "all"}


def url(name: str, record: Record, /, **params: str) -> str:
    query = f"?{urlencode(params)}" if params else ""
    return reverse(name, args=[record.pk]) + query


def page(client: Client, address: str) -> str:
    response = client.get(address)
    assert response.status_code == 200, response.content.decode()[:500]
    return html.unescape(response.content.decode())


def birth(eph: Ephemeris, scale: TimeScale = TimeScale.UT1_FROM_IERS) -> AstronomicalTime:
    return eph.time(resolve(GOOD.time, GOOD.place), scale)


def engine(
    eph: Ephemeris, kind: EclipseKind, types: frozenset[EclipseType], scale: TimeScale
) -> tuple[Eclipse, Eclipse]:
    time = birth(eph, scale)
    return previous_eclipse(eph, time, kind, types), next_eclipse(eph, time, kind, types)


def block(text: str, name: str) -> str:
    found = re.search(rf'data-eclipse="{name}"(.*?)(?=data-eclipse="|</main>)', text, re.S)
    assert found is not None, name
    return found.group(1)


def cell(text: str, name: str) -> str:
    found = re.search(rf'data-degree="{name}">([^<]+)<', text)
    assert found is not None, name
    return found.group(1)


def shown_blocks(text: str) -> list[str]:
    return re.findall(r'data-eclipse="([a-z]+-[a-z]+)"', text)


# --- Access and types -----------------------------------------------------------------------


@pytest.mark.django_db
def test_eclipses_need_a_login(client: Client, store: Callable[..., Record]) -> None:
    response = client.get(url("record-eclipses", store(GOOD), **EVERY_TYPE))
    assert response.status_code == 302 and "login" in response["Location"]


@pytest.mark.django_db
def test_without_types_the_page_asks_for_them(
    admin_client: Client, store: Callable[..., Record]
) -> None:
    text = page(admin_client, url("record-eclipses", store(GOOD)))
    assert 'name="solar"' in text and 'name="lunar"' in text
    assert shown_blocks(text) == []  # no types are ever picked for you


@pytest.mark.django_db
def test_a_kind_is_shown_only_when_its_types_are_named(
    admin_client: Client, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    assert shown_blocks(page(admin_client, url("record-eclipses", record, solar="all"))) == [
        "solar-before",
        "solar-after",
    ]
    assert shown_blocks(page(admin_client, url("record-eclipses", record, lunar="all"))) == [
        "lunar-before",
        "lunar-after",
    ]


@pytest.mark.django_db
def test_all_is_every_type_of_the_kind(admin_client: Client, store: Callable[..., Record]) -> None:
    record = store(GOOD)
    named = {
        "solar": ",".join(sorted(t.value for t in SOLAR_TYPES)),
        "lunar": ",".join(sorted(t.value for t in LUNAR_TYPES)),
    }
    every = page(admin_client, url("record-eclipses", record, **EVERY_TYPE))
    listed = page(admin_client, url("record-eclipses", record, **named))
    for name in ("solar-before", "solar-after", "lunar-before", "lunar-after"):
        assert block(every, name) == block(listed, name)


@pytest.mark.django_db
def test_the_chosen_types_decide_which_eclipses_count(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record]
) -> None:
    # 1987-06-15: the next solar eclipse is annular (1987-09-23); the next total one comes later.
    before, after = engine(
        eph, EclipseKind.SOLAR, frozenset({EclipseType.TOTAL}), TimeScale.UT1_FROM_IERS
    )
    text = page(admin_client, url("record-eclipses", store(GOOD), solar="total"))
    for name, eclipse in (("solar-before", before), ("solar-after", after)):
        assert eclipse.type is EclipseType.TOTAL
        assert eclipse.greatest.time.instant.utc.isoformat() in block(text, name)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("types", "reason"),
    [
        ({"solar": "ring"}, "'ring' is not"),
        ({"solar": "penumbral"}, "solar eclipse types must be a non-empty choice from"),
        ({"lunar": "annular"}, "lunar eclipse types must be a non-empty choice from"),
        ({"solar": "total,total"}, "repeat"),
    ],
    ids=["unknown type", "a lunar type as solar", "a solar type as lunar", "repeated"],
)
def test_types_the_engine_can_t_use_are_refused_in_place(
    admin_client: Client, store: Callable[..., Record], types: dict[str, str], reason: str
) -> None:
    response = admin_client.get(url("record-eclipses", store(GOOD), **types))
    assert response.status_code == 400
    text = html.unescape(response.content.decode())
    box = re.search(r'data-error="request"[^>]*>(.*?)</div>', text, re.S)
    assert box is not None and reason in box.group(1)
    for name, value in types.items():
        assert re.search(rf'name="{name}"[^>]*value="{re.escape(value)}"', text), name
    assert shown_blocks(text) == []


# --- What each eclipse shows ----------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("scale", [TimeScale.UT1_FROM_IERS, TimeScale.CLOCK_AS_UT1])
def test_each_eclipse_shows_the_engine_s_values(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record], scale: TimeScale
) -> None:
    names = {TimeScale.UT1_FROM_IERS: "ut1-from-iers", TimeScale.CLOCK_AS_UT1: "clock-as-ut1"}
    text = page(
        admin_client, url("record-eclipses", store(GOOD), **EVERY_TYPE, time_scale=names[scale])
    )
    born = birth(eph, scale)
    for kind, types in ((EclipseKind.SOLAR, SOLAR_TYPES), (EclipseKind.LUNAR, LUNAR_TYPES)):
        for side, eclipse in zip(("before", "after"), engine(eph, kind, types, scale), strict=True):
            shown = block(text, f"{kind.value}-{side}")
            assert eclipse.type.value in shown
            assert eclipse.greatest.time.instant.utc.isoformat() in shown
            assert eclipse.syzygy.time.instant.utc.isoformat() in shown
            assert f"{eclipse.greatest.time.jd_tt - born.jd_tt:.6f}" in shown  # days from birth
            degree = eclipse.syzygy.sun if kind is EclipseKind.SOLAR else eclipse.syzygy.moon
            # The Sun and Moon at both instants appear too, so the degree's own cells are read.
            assert cell(shown, "longitude") == f"{degree.longitude:.6f}"
            assert cell(shown, "declination") == f"{degree.declination:.6f}"
            assert f"{eclipse.magnitude:.6f}" in shown
            assert f"{eclipse.saros_series}" in shown and f"{eclipse.saros_member}" in shown
            for instant in (eclipse.greatest, eclipse.syzygy):
                for body in (instant.sun, instant.moon):
                    assert f"{body.longitude:.6f}" in shown
            if kind is EclipseKind.SOLAR:
                assert eclipse.place_of_greatest is not None
                assert f"{eclipse.place_of_greatest.latitude:.4f}" in shown
                assert f"{eclipse.place_of_greatest.longitude:.4f}" in shown
                assert ("central" if eclipse.central else "not central") in shown
            else:
                assert eclipse.penumbral_magnitude is not None
                assert f"{eclipse.penumbral_magnitude:.6f}" in shown


@pytest.mark.django_db
def test_the_page_says_which_degree_an_eclipse_has(
    admin_client: Client, store: Callable[..., Record]
) -> None:
    text = page(admin_client, url("record-eclipses", store(GOOD), **EVERY_TYPE))
    assert "at the syzygy: the Sun's for a solar eclipse, the Moon's for a lunar one" in text


# --- Refusals -------------------------------------------------------------------------------


@pytest.mark.django_db
def test_an_eclipse_the_engine_refuses_shows_its_refusal_in_its_place(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record]
) -> None:
    # From 2026-09-01 the next solar and lunar eclipses are past IERS's last day, 2026-10-10;
    # the ones before it are not, and still show.
    late = BirthRecord(time=UtcTime(utc=datetime(2026, 9, 1, tzinfo=UTC)), place=LONDON)
    text = page(admin_client, url("record-eclipses", store(late), **EVERY_TYPE))
    assert shown_blocks(text) == ["solar-before", "solar-after", "lunar-before", "lunar-after"]
    time = eph.time(resolve(late.time, late.place), TimeScale.UT1_FROM_IERS)
    for kind, types in ((EclipseKind.SOLAR, SOLAR_TYPES), (EclipseKind.LUNAR, LUNAR_TYPES)):
        before = previous_eclipse(eph, time, kind, types)
        assert before.greatest.time.instant.utc.isoformat() in block(text, f"{kind.value}-before")
        after = block(text, f"{kind.value}-after")
        assert "DateOutsideUt1Data" in after and "2026-10-10" in after


@pytest.mark.django_db
def test_eclipses_outside_the_data_files_are_refused(
    admin_client: Client, store: Callable[..., Record]
) -> None:
    early = BirthRecord(time=UtcTime(utc=datetime(1700, 1, 1, 12, tzinfo=UTC)), place=LONDON)
    text = page(admin_client, url("record-eclipses", store(early), **EVERY_TYPE))
    for name in ("solar-before", "solar-after", "lunar-before", "lunar-after"):
        assert "DateOutsideEphemeris" in block(text, name)


@pytest.mark.django_db
def test_a_refused_record_shows_its_refusal(
    admin_client: Client, store: Callable[..., Record]
) -> None:
    ambiguous = BirthRecord(
        time=ZonedLocalTime(local=datetime(1987, 10, 25, 1, 30), zone="Europe/London"),
        place=LONDON,
    )
    text = page(admin_client, url("record-eclipses", store(ambiguous), **EVERY_TYPE))
    assert "AmbiguousLocalTime" in text and shown_blocks(text) == []


@pytest.mark.django_db
def test_an_unknown_record_is_not_found(admin_client: Client) -> None:
    address = reverse("record-eclipses", args=[999_999]) + f"?{urlencode(EVERY_TYPE)}"
    assert admin_client.get(address).status_code == 404


# --- Getting there --------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_chart_page_and_the_record_s_admin_page_link_to_it(
    admin_client: Client, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    path = reverse("record-eclipses", args=[record.pk])
    chart = page(admin_client, url("record-chart", record, time_scale="clock-as-ut1"))
    assert re.search(rf'href="{re.escape(path)}\?[^"]*time_scale=clock-as-ut1', chart)
    change = page(admin_client, f"/admin/research_platform/record/{record.pk}/change/")
    assert f'href="{path}"' in change
