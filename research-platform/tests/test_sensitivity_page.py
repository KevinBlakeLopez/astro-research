"""A record's birth-time sensitivity: every measurement recomputed across a span of offsets.

The page runs the engine's `sweep()` on request. The offsets come from a span and a step, in
seconds, in the address (`span=600&step=60` is +-10 minutes every minute), and are never
defaulted. A sweep is capped at 241 offsets and a 2-hour span. The series are listed from the
widest range of change to the narrowest, the first 100 unless all are asked for. They can be
narrowed by quantity, point, aspect angle and whether the Earth's rotation drives them. One
series' values and changes at every offset are a fragment away, with a plot, and every row
downloads as CSV.

Markup the tests rely on: each series row carries data-series="quantity|a|b|angle", with b and
angle empty when the series has none, and each detail row data-offset="<seconds>".
"""

import csv
import io
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from astro_research.chart import BirthRecord, ChartSettings
from astro_research.civil_time import UtcTime, ZonedLocalTime
from astro_research.ephemeris import Ephemeris
from astro_research.geo import GeoPosition
from astro_research.measurements import MAJOR
from astro_research.sensitivity import MeasurementSeries, SensitivitySweep, sweep
from research_platform.models import Record

LONDON = GeoPosition(latitude=51.5074, longitude=-0.1278)
GOOD = BirthRecord(
    time=ZonedLocalTime(local=datetime(1987, 6, 15, 4, 25), zone="Europe/London"), place=LONDON
)
AMBIGUOUS = BirthRecord(
    time=ZonedLocalTime(local=datetime(1987, 10, 25, 1, 30), zone="Europe/London"), place=LONDON
)
TEN_MINUTES = {"aspects": "major", "span": "600", "step": "60"}
NAMES = ("record-sensitivity", "record-sensitivity-series", "record-sensitivity-csv")


def url(name: str, record: Record, /, **params: str) -> str:
    query = f"?{urlencode(params)}" if params else ""
    return reverse(name, args=[record.pk]) + query


def page(client: Client, address: str, **headers: str) -> str:
    response = client.get(address, headers=headers)
    assert response.status_code == 200, response.content.decode()[:500]
    text: str = response.content.decode()
    return text


def engine_sweep(eph: Ephemeris, birth_record: BirthRecord = GOOD) -> SensitivitySweep:
    offsets = [timedelta(minutes=minutes) for minutes in range(-10, 11)]
    return sweep(eph, birth_record, ChartSettings(aspect_angles=MAJOR), offsets)


def series_id(series: MeasurementSeries) -> str:
    a = series.points[0].value
    b = series.points[1].value if len(series.points) == 2 else ""
    angle = "" if series.aspect_angle is None else repr(series.aspect_angle)
    return f"{series.quantity.value}|{a}|{b}|{angle}"


def shown_series(html: str) -> list[str]:
    return re.findall(r'data-series="([^"]+)"', html)


# --- Access and offsets ---------------------------------------------------------------------


@pytest.mark.django_db
def test_sensitivity_needs_a_login(client: Client, store: Callable[..., Record]) -> None:
    record = store(GOOD)
    for name in (*NAMES, "record-sensitivity-detail"):
        response = client.get(url(name, record, **TEN_MINUTES))
        assert response.status_code == 302 and "login" in response["Location"], name


@pytest.mark.django_db
def test_without_a_span_and_step_the_page_asks_for_them(
    admin_client: Client, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    html = page(admin_client, url("record-sensitivity", record, aspects="major"))
    assert 'name="span"' in html and 'name="step"' in html
    assert "data-series=" not in html  # no offsets are ever picked for you


@pytest.mark.django_db
@pytest.mark.parametrize(
    "offsets",
    [
        {"span": "600", "step": "70"},
        {"span": "600", "step": "0"},
        {"span": "-600", "step": "60"},
        {"span": "ten", "step": "60"},
        {"span": "600"},
        {"span": "7260", "step": "660"},
        {"span": "7200", "step": "50"},
    ],
    ids=[
        "span not a multiple of step",
        "zero step",
        "negative span",
        "not a number",
        "span without step",
        "over two hours (23 offsets)",
        "over 241 offsets (289, within two hours)",
    ],
)
def test_offsets_the_page_won_t_sweep_are_refused(
    admin_client: Client, store: Callable[..., Record], offsets: dict[str, str]
) -> None:
    record = store(GOOD)
    for name in NAMES:
        response = admin_client.get(url(name, record, aspects="major", **offsets))
        assert response.status_code == 400, name


@pytest.mark.django_db
def test_the_largest_sweep_allowed_runs(admin_client: Client, store: Callable[..., Record]) -> None:
    record = store(GOOD)
    html = page(admin_client, url("record-sensitivity", record, span="7200", step="60"))
    assert "241 offsets" in html


# --- The page -------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_page_lists_the_engine_s_series_widest_first(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    swept = engine_sweep(eph)
    html = page(admin_client, url("record-sensitivity", record, **TEN_MINUTES))
    assert "21 offsets" in html
    assert swept.base.calculation_key in html
    widest = swept.by_range()[:100]
    assert shown_series(html) == [series_id(series) for series in widest]
    for series in widest[:5]:
        assert f"{series.range * 3600:.3f}" in html and f"{series.base_rate:.6f}" in html


@pytest.mark.django_db
def test_every_series_can_be_shown(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    html = page(admin_client, url("record-sensitivity", record, **TEN_MINUTES, limit="all"))
    assert shown_series(html) == [series_id(s) for s in engine_sweep(eph).by_range()]


@pytest.mark.django_db
def test_the_series_are_narrowed_by_a_fragment(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    filters = {"quantity": "deviation", "angle": "90", "involving": "moon", "rotation": "none"}
    address = url("record-sensitivity-series", record, **TEN_MINUTES, **filters)
    html = page(admin_client, address, HX_Request="true")
    assert "<html" not in html
    expected = [
        series
        for series in engine_sweep(eph).by_range()
        if series.quantity.value == "deviation"
        and series.aspect_angle == 90.0
        and "moon" in {point.value for point in series.points}
        and not series.rotation_driven
    ]
    assert expected and shown_series(html) == [series_id(series) for series in expected]


@pytest.mark.django_db
def test_the_series_can_be_narrowed_to_one_quantity(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    address = url("record-sensitivity-series", record, **TEN_MINUTES, quantity="declination")
    html = page(admin_client, address, HX_Request="true")
    declinations = [s for s in engine_sweep(eph).by_range() if s.quantity.value == "declination"]
    assert len(declinations) == 17
    assert shown_series(html) == [series_id(series) for series in declinations]


@pytest.mark.django_db
def test_only_the_rotation_driven_series_can_be_shown(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    address = url("record-sensitivity-series", record, **TEN_MINUTES, rotation="only", limit="all")
    html = page(admin_client, address, HX_Request="true")
    rotation = [s for s in engine_sweep(eph).by_range() if s.rotation_driven]
    assert shown_series(html) == [series_id(series) for series in rotation]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "filters",
    [
        {"quantity": "brightness"},
        {"involving": "chiron"},
        {"angle": "45"},
        {"rotation": "sometimes"},
        {"limit": "lots"},
    ],
    ids=[
        "unknown quantity",
        "unknown point",
        "an angle not named",
        "unknown rotation",
        "bad limit",
    ],
)
def test_a_filter_the_sweep_can_t_apply_is_refused(
    admin_client: Client, store: Callable[..., Record], filters: dict[str, str]
) -> None:
    record = store(GOOD)
    address = url("record-sensitivity-series", record, **TEN_MINUTES, **filters)
    assert admin_client.get(address).status_code == 400


@pytest.mark.django_db
def test_a_sweep_across_the_sidereal_time_switch_says_so(
    admin_client: Client, store: Callable[..., Record]
) -> None:
    # Swiss Ephemeris changes sidereal-time model at 2050-01-01 0h UT1, where the angles jump
    # by 1.9 arcsec. The clock convention is used: the other scales refuse dates that late.
    near = store(
        BirthRecord(time=UtcTime(utc=datetime(2049, 12, 31, 23, 59, tzinfo=UTC)), place=LONDON)
    )
    params = {"span": "120", "step": "60", "time_scale": "clock-as-ut1"}
    assert 'data-warning="sidereal-time-switch"' in page(
        admin_client, url("record-sensitivity", near, **params)
    )
    good = store(GOOD)
    assert "data-warning=" not in page(admin_client, url("record-sensitivity", good, **params))


@pytest.mark.django_db
def test_a_refused_record_or_offset_shows_the_refusal(
    admin_client: Client, store: Callable[..., Record]
) -> None:
    ambiguous = store(AMBIGUOUS)
    html = page(admin_client, url("record-sensitivity", ambiguous, **TEN_MINUTES))
    assert "AmbiguousLocalTime" in html and "data-series=" not in html
    # Ten minutes after this one is past IERS's last day, 2026-10-10.
    late = store(
        BirthRecord(time=UtcTime(utc=datetime(2026, 10, 9, 23, 55, tzinfo=UTC)), place=LONDON)
    )
    html = page(admin_client, url("record-sensitivity", late, **TEN_MINUTES))
    assert "DateOutsideUt1Data" in html and "data-series=" not in html
    assert admin_client.get(url("record-sensitivity-csv", late, **TEN_MINUTES)).status_code == 422


@pytest.mark.django_db
def test_an_unknown_record_is_not_found(admin_client: Client) -> None:
    for name in (*NAMES, "record-sensitivity-detail"):
        address = reverse(name, args=[999_999]) + f"?{urlencode(TEN_MINUTES)}"
        assert admin_client.get(address).status_code == 404, name


# --- One series -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_one_series_shows_its_value_and_change_at_every_offset(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    swept = engine_sweep(eph)
    series = next(s for s in swept.series if series_id(s) == "deviation|moon|ascendant|90.0")
    params = {**TEN_MINUTES, "quantity": "deviation", "a": "moon", "b": "ascendant", "angle": "90"}
    html = page(admin_client, url("record-sensitivity-detail", record, **params), HX_Request="true")
    offsets = re.findall(r'data-offset="(-?\d+)"', html)
    assert offsets == [str(int(offset.total_seconds())) for offset in swept.offsets]
    for value, change in zip(series.values, series.changes, strict=True):
        assert f"{value:.6f}" in html and f"{change * 3600:.3f}" in html
    plot = re.search(r'<polyline[^>]*points="([^"]+)"', html)
    assert plot is not None and len(plot.group(1).split()) == len(swept.offsets)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "series",
    [
        {"quantity": "longitude", "a": "sun", "b": "moon"},
        {"quantity": "separation", "a": "moon", "b": "sun"},
        {"quantity": "deviation", "a": "sun", "b": "moon"},
        {"quantity": "separation", "a": "sun"},
    ],
    ids=["a point's series with a second point", "a pair out of order", "no angle", "no b"],
)
def test_a_series_the_sweep_doesn_t_have_is_refused(
    admin_client: Client, store: Callable[..., Record], series: dict[str, str]
) -> None:
    record = store(GOOD)
    address = url("record-sensitivity-detail", record, **TEN_MINUTES, **series)
    assert admin_client.get(address).status_code == 400


# --- The download ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_every_row_downloads_as_csv(
    admin_client: Client, eph: Ephemeris, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    swept = engine_sweep(eph)
    response = admin_client.get(url("record-sensitivity-csv", record, **TEN_MINUTES))
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    name = f"{swept.base.calculation_key}-span600-step60.csv"
    assert f'filename="{name}"' in response["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(response.content.decode())))
    assert rows[0] == [
        "offset_s",
        "quantity",
        "point_a",
        "point_b",
        "aspect_angle",
        "rotation_driven",
        "value",
        "change",
    ]
    expected = swept.rows()
    assert len(rows) - 1 == len(expected)
    for written, row in zip(rows[1:], expected, strict=True):
        assert written == [
            repr(row.offset_s),
            row.quantity.value,
            row.point_a.value,
            "" if row.point_b is None else row.point_b.value,
            "" if row.aspect_angle is None else repr(row.aspect_angle),
            "true" if row.rotation_driven else "false",
            repr(row.value),
            repr(row.change),
        ]


# --- Getting there --------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_chart_page_and_the_record_s_admin_page_link_to_it(
    admin_client: Client, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    chart = page(admin_client, url("record-chart", record, aspects="major"))
    assert f'href="{reverse("record-sensitivity", args=[record.pk])}?aspects=major' in chart
    change = page(admin_client, f"/admin/research_platform/record/{record.pk}/change/")
    assert f'href="{reverse("record-sensitivity", args=[record.pk])}"' in change


@pytest.mark.django_db
def test_the_page_drives_its_fragments_with_htmx(
    admin_client: Client, store: Callable[..., Record]
) -> None:
    record = store(GOOD)
    html = page(admin_client, url("record-sensitivity", record, **TEN_MINUTES))
    assert f'hx-get="{reverse("record-sensitivity-series", args=[record.pk])}"' in html
    assert f'hx-get="{reverse("record-sensitivity-detail", args=[record.pk])}' in html
    scripts = re.findall(r'<script[^>]*src="([^"]+)"', html)
    assert scripts == ["/static/research_platform/htmx-2.0.11.min.js"]
