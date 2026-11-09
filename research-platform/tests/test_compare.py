"""Comparing two records' charts: B minus A, for every number, on one page.

Any two records can be compared: twins, or two sources' records of one subject. Both charts are
computed on request under the settings in the address, as `compare()` requires, and every change
is B minus A. Changes are continuous; nothing says a contact appeared or disappeared. The pairs
can be narrowed to those involving an angle or one point, and sorted by the size of their change
or by the closer of the two charts' deviations from one aspect angle. Two records are picked in
the admin.

Markup the tests rely on: each point row carries data-point="<point>", each pair row
data-pair="a-b", and each deviation cell data-angle="<angle>".
"""

import re
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from astro_research.astronomical_time import TimeScale
from astro_research.chart import BirthRecord, ChartSettings, compute_chart
from astro_research.civil_time import ZonedLocalTime
from astro_research.comparison import ChartComparison, PairChange, compare
from astro_research.ephemeris import Ephemeris
from astro_research.geo import GeoPosition
from astro_research.measurements import MAJOR
from research_platform.models import Record, Subject

LONDON = GeoPosition(latitude=51.5074, longitude=-0.1278)
FIRST = BirthRecord(
    time=ZonedLocalTime(local=datetime(1987, 6, 15, 4, 25), zone="Europe/London"), place=LONDON
)
SECOND = BirthRecord(
    time=ZonedLocalTime(local=datetime(1987, 6, 15, 4, 26), zone="Europe/London"), place=LONDON
)
AMBIGUOUS = BirthRecord(
    time=ZonedLocalTime(local=datetime(1987, 10, 25, 1, 30), zone="Europe/London"), place=LONDON
)


@pytest.fixture
def twins(store: Callable[..., Record]) -> tuple[Record, Record]:
    second = Subject.objects.create(label="subject-1-twin")
    return store(FIRST), store(SECOND, subject=second)


def address(name: str, a: Record, b: Record, /, **params: str) -> str:
    query = f"?{urlencode(params)}" if params else ""
    return reverse(name, args=[a.pk, b.pk]) + query


def page(client: Client, url: str, **headers: str) -> str:
    response = client.get(url, headers=headers)
    assert response.status_code == 200, response.content.decode()[:500]
    text: str = response.content.decode()
    return text


def engine(eph: Ephemeris, settings: ChartSettings) -> ChartComparison:
    return compare(compute_chart(eph, FIRST, settings), compute_chart(eph, SECOND, settings))


def arcsec(degrees: float) -> str:
    return f"{degrees * 3600:.3f}"


def names(change: PairChange) -> tuple[str, str]:
    return change.a.a.point.value, change.a.b.point.value


def shown_pairs(html: str) -> list[tuple[str, str]]:
    return re.findall(r'data-pair="([a-z_]+)-([a-z_]+)"', html)


# --- The page -------------------------------------------------------------------------------


@pytest.mark.django_db
def test_comparing_needs_a_login(client: Client, twins: tuple[Record, Record]) -> None:
    for name in ("record-compare", "record-compare-pairs"):
        response = client.get(address(name, *twins))
        assert response.status_code == 302 and "login" in response["Location"], name


@pytest.mark.django_db
def test_the_page_shows_the_engine_s_comparison(
    admin_client: Client, eph: Ephemeris, twins: tuple[Record, Record]
) -> None:
    comparison = engine(eph, ChartSettings(aspect_angles=MAJOR))
    html = page(admin_client, address("record-compare", *twins, aspects="major"))
    assert comparison.a.time.instant.utc.isoformat() in html
    assert comparison.b.time.instant.utc.isoformat() in html
    assert f"{comparison.utc_seconds:.6f}" in html and f"{comparison.tt_seconds:.6f}" in html
    assert arcsec(comparison.armc) in html
    assert len(re.findall(r'data-point="', html)) == len(comparison.points)
    for point in comparison.points:
        assert f"{point.a.longitude:.6f}" in html and f"{point.b.longitude:.6f}" in html
        assert arcsec(point.longitude) in html and arcsec(point.declination) in html
    assert len(shown_pairs(html)) == len(comparison.pairs)
    first = comparison.pairs[0]
    assert arcsec(first.separation) in html
    for deviation in first.deviations:
        assert f'data-angle="{deviation.angle}"' in html
        assert f"{deviation.a.deviation:.6f}" in html and f"{deviation.b.deviation:.6f}" in html


@pytest.mark.django_db
def test_without_aspect_angles_there_are_no_deviations(
    admin_client: Client, twins: tuple[Record, Record]
) -> None:
    html = page(admin_client, address("record-compare", *twins))
    assert len(shown_pairs(html)) == 136
    assert "data-angle=" not in html
    assert 'name="aspects"' in html


@pytest.mark.django_db
def test_changes_are_b_minus_a_and_the_page_can_swap_them(
    admin_client: Client, eph: Ephemeris, twins: tuple[Record, Record]
) -> None:
    a, b = twins
    forward = page(admin_client, address("record-compare", a, b, aspects="major"))
    backward = page(admin_client, address("record-compare", b, a, aspects="major"))
    assert "60.000000" in forward and "-60.000000" in backward
    swap = reverse("record-compare", args=[b.pk, a.pk])
    assert re.search(rf'href="{re.escape(swap)}\?[^"]*aspects=major', forward)


@pytest.mark.django_db
def test_the_settings_come_from_the_address(
    admin_client: Client, eph: Ephemeris, twins: tuple[Record, Record]
) -> None:
    settings = ChartSettings(aspect_angles=(45.0,), time_scale=TimeScale.CLOCK_AS_UT1)
    comparison = engine(eph, settings)
    html = page(
        admin_client, address("record-compare", *twins, aspects="45", time_scale="clock-as-ut1")
    )
    assert arcsec(comparison.armc) in html
    assert 'data-angle="45.0"' in html and 'data-angle="90.0"' not in html


@pytest.mark.django_db
def test_each_chart_s_document_is_a_link_away(
    admin_client: Client, twins: tuple[Record, Record]
) -> None:
    html = page(admin_client, address("record-compare", *twins, aspects="major"))
    for record in twins:
        assert f'href="{reverse("record-chart-json", args=[record.pk])}?aspects=major' in html
        assert f'href="{reverse("record-chart", args=[record.pk])}?aspects=major' in html


@pytest.mark.django_db
@pytest.mark.parametrize("params", [{"aspects": "banana"}, {"time_scale": "sidereal"}])
def test_settings_the_engine_can_t_use_are_refused(
    admin_client: Client, twins: tuple[Record, Record], params: dict[str, str]
) -> None:
    for name in ("record-compare", "record-compare-pairs"):
        assert admin_client.get(address(name, *twins, **params)).status_code == 400, name


@pytest.mark.django_db
def test_a_refused_record_is_shown_instead_of_a_comparison(
    admin_client: Client, store: Callable[..., Record], twins: tuple[Record, Record]
) -> None:
    refused = store(AMBIGUOUS, subject=Subject.objects.create(label="subject-3"))
    for a, b in ((twins[0], refused), (refused, twins[0])):
        html = page(admin_client, address("record-compare", a, b, aspects="major"))
        assert "AmbiguousLocalTime" in html and "subject-3" in html
        assert "data-pair=" not in html and "data-point=" not in html
    assert admin_client.get(address("record-compare-pairs", twins[0], refused)).status_code == 422


@pytest.mark.django_db
def test_an_unknown_record_is_not_found(admin_client: Client, twins: tuple[Record, Record]) -> None:
    for name in ("record-compare", "record-compare-pairs"):
        assert admin_client.get(reverse(name, args=[twins[0].pk, 999_999])).status_code == 404


# --- The pairs fragment ---------------------------------------------------------------------


@pytest.mark.django_db
def test_the_pairs_can_be_narrowed_to_those_involving_an_angle(
    admin_client: Client, eph: Ephemeris, twins: tuple[Record, Record]
) -> None:
    comparison = engine(eph, ChartSettings(aspect_angles=MAJOR))
    url = address("record-compare-pairs", *twins, aspects="major", involving="angles")
    html = page(admin_client, url, HX_Request="true")
    assert "<html" not in html
    assert shown_pairs(html) == [names(change) for change in comparison.involving_angles()]


@pytest.mark.django_db
def test_the_pairs_can_be_narrowed_to_one_point_and_sorted_by_their_change(
    admin_client: Client, eph: Ephemeris, twins: tuple[Record, Record]
) -> None:
    comparison = engine(eph, ChartSettings(aspect_angles=MAJOR))
    url = address("record-compare-pairs", *twins, aspects="major", involving="moon", sort="change")
    html = page(admin_client, url, HX_Request="true")
    moon = [change for change in comparison.pairs if "moon" in names(change)]
    largest_first = sorted(moon, key=lambda change: -abs(change.separation))
    assert shown_pairs(html) == [names(change) for change in largest_first]


@pytest.mark.django_db
def test_the_pairs_can_be_sorted_by_the_closer_of_the_two_deviations(
    admin_client: Client, eph: Ephemeris, twins: tuple[Record, Record]
) -> None:
    comparison = engine(eph, ChartSettings(aspect_angles=MAJOR))
    url = address("record-compare-pairs", *twins, aspects="major", sort="deviation", angle="90")
    html = page(admin_client, url, HX_Request="true")
    index = MAJOR.index(90.0)

    def closer(change: PairChange) -> float:
        deviation = change.deviations[index]
        return min(abs(deviation.a.deviation), abs(deviation.b.deviation))

    assert shown_pairs(html) == [names(c) for c in sorted(comparison.pairs, key=closer)]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "params",
    [
        {"aspects": "major", "involving": "chiron"},
        {"aspects": "major", "sort": "brightness"},
        {"aspects": "major", "sort": "deviation", "angle": "45"},
        {"aspects": "major", "sort": "deviation"},
    ],
    ids=["unknown point", "unknown order", "an angle not named", "deviation with no angle"],
)
def test_a_filter_the_comparison_can_t_apply_is_refused(
    admin_client: Client, twins: tuple[Record, Record], params: dict[str, str]
) -> None:
    assert admin_client.get(address("record-compare-pairs", *twins, **params)).status_code == 400


@pytest.mark.django_db
def test_the_page_drives_the_fragment_with_htmx(
    admin_client: Client, twins: tuple[Record, Record]
) -> None:
    html = page(admin_client, address("record-compare", *twins, aspects="major"))
    assert f'hx-get="{reverse("record-compare-pairs", args=[twins[0].pk, twins[1].pk])}"' in html
    scripts = re.findall(r'<script[^>]*src="([^"]+)"', html)
    assert scripts == ["/static/research_platform/htmx-2.0.11.min.js"]


# --- Picking two records in the admin -------------------------------------------------------


CHANGELIST = "/admin/research_platform/record/"


@pytest.mark.django_db
def test_two_records_chosen_in_the_admin_open_their_comparison(
    admin_client: Client, twins: tuple[Record, Record]
) -> None:
    a, b = twins
    response = admin_client.post(
        CHANGELIST, {"action": "compare_records", "_selected_action": [b.pk, a.pk]}
    )
    assert response.status_code == 302
    assert response["Location"] == reverse("record-compare", args=[a.pk, b.pk])  # older first


@pytest.mark.django_db
@pytest.mark.parametrize("count", [1, 3])
def test_comparing_needs_exactly_two_records(
    admin_client: Client, store: Callable[..., Record], twins: tuple[Record, Record], count: int
) -> None:
    third = store(SECOND, subject=Subject.objects.create(label="subject-4"))
    chosen = [*twins, third][:count]
    response = admin_client.post(
        CHANGELIST,
        {"action": "compare_records", "_selected_action": [r.pk for r in chosen]},
        follow=True,
    )
    assert response.redirect_chain[-1][0].endswith(CHANGELIST)
    assert "Choose exactly two records to compare" in response.content.decode()
