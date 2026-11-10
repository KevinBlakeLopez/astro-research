"""Derived configurations ("features"): what you define from a chart, from a registry of kinds.

A `Feature` is a name, a kind and the kind's settings, which the kind checks. The first kinds:

- contact (yes/no): a point, other points, aspect angles and an orb. Yes when, for any of the
  other points and any angle, |ecliptic separation - angle| <= orb, the orb's edge included.
  Mars to the Ascendant or MC at 0 or 180 within 10 is "Mars on an angle" (180 from the
  Ascendant is the Descendant, from the MC the IC). This isn't Gauquelin's sector definition.
- sign (category): the point's tropical zodiac sign, its longitude in 30-degree steps.

A feature's value comes from a stored chart when needed and is never stored. Its page shows,
for a run you choose, each charted record's value and the counts; refused records have none.
Features are entered on the platform's own pages, since its users won't be developers.

Markup the tests rely on: the list's rows carry data-feature with the feature's id; a run's
value rows carry data-record with the record's id and data-value with the value (yes, no or a
sign); the counts are in the element with class "counts".
"""

import html
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from astro_research.chart import BirthRecord, Chart, ChartSettings
from astro_research.civil_time import UtcTime, ZonedLocalTime
from astro_research.ephemeris import Ephemeris
from astro_research.geo import GeoPosition
from astro_research.measurements import MAJOR
from research_platform.features import POINTS, SIGNS, sign_of
from research_platform.models import (
    CalculationRun,
    Dataset,
    Feature,
    Record,
    StoredChart,
    Subject,
)
from research_platform.runner import run_dataset

LONDON = GeoPosition(latitude=51.5074, longitude=-0.1278)
FIRST = BirthRecord(
    time=ZonedLocalTime(local=datetime(1987, 6, 15, 4, 25), zone="Europe/London"), place=LONDON
)
SECOND = BirthRecord(
    time=UtcTime(utc=datetime(2024, 3, 20, 12, tzinfo=UTC)),
    place=GeoPosition(latitude=-33.8688, longitude=151.2093),
)
AMBIGUOUS = BirthRecord(  # 01:30 happens twice as British Summer Time ends
    time=ZonedLocalTime(local=datetime(1987, 10, 25, 1, 30), zone="Europe/London"), place=LONDON
)
ON_AN_ANGLE = {"point": "mars", "others": ["ascendant", "midheaven"], "angles": [0.0, 180.0]}
CONTACT_FORM = {
    "name": "Mars on an angle",
    "description": "Ecliptic orb, not Gauquelin's sectors",
    "point": "mars",
    "others": ["midheaven", "ascendant"],  # stored in the chart's order
    "angles": "180, 0",  # stored sorted
    "orb": "10",
}


@pytest.fixture
def run(store: Callable[..., Record], eph: Ephemeris) -> CalculationRun:
    """Three records: two charts and one refusal (observed)."""
    records = [
        store(FIRST),
        store(AMBIGUOUS, subject=Subject.objects.create(label="subject-2")),
        store(SECOND, subject=Subject.objects.create(label="subject-3")),
    ]
    dataset = Dataset.objects.create(name="sample")
    dataset.records.add(*records)
    return run_dataset(dataset, ChartSettings(aspect_angles=MAJOR), eph)


def charted(run: CalculationRun) -> list[tuple[Record, Chart]]:
    outcomes = run.outcomes.select_related("record", "chart").order_by("record__pk")
    return [(o.record, o.chart.chart()) for o in outcomes if o.chart is not None]


def separation(chart: Chart, a: str, b: str) -> float:
    """The chart's own ecliptic separation of two points, whichever way round it lists them."""
    for pair in chart.pairs:
        if {pair.a.point.value, pair.b.point.value} == {a, b}:
            return pair.separation
    raise AssertionError(f"no pair {a}-{b}")


def longitude(chart: Chart, name: str) -> float:
    return next(p.longitude for p in chart.points if p.point.value == name)


def contact(orb: float, **settings: Any) -> Feature:
    feature = Feature(
        name="contact", kind="contact", settings={**ON_AN_ANGLE, **settings, "orb": orb}
    )
    feature.full_clean()
    return feature


def page(client: Client, address: str) -> str:
    response = client.get(address)
    assert response.status_code == 200, response.content.decode()[:500]
    return html.unescape(response.content.decode())


# --- Contact --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_contact_is_yes_up_to_its_orb_s_edge(run: CalculationRun) -> None:
    (_, chart), *_ = charted(run)
    gap = separation(chart, "mars", "ascendant")
    angle = 180.0 if gap > 90 else 0.0
    edge = abs(gap - angle)
    settings = {"others": ["ascendant"], "angles": [angle]}
    assert contact(edge, **settings).value(chart) is True
    assert contact(math.nextafter(edge, 0.0), **settings).value(chart) is False


@pytest.mark.django_db
def test_a_contact_is_yes_for_any_of_its_points_and_angles(run: CalculationRun) -> None:
    (_, first), (_, second) = charted(run)
    deviations = {
        (other, angle): abs(separation(first, "mars", other) - angle)
        for other in ("ascendant", "midheaven")
        for angle in (0.0, 180.0)
    }
    closest = min(deviations.values())
    feature = contact(closest)
    assert feature.value(first) is True  # only the closest of the four combinations is in orb
    expected = any(
        abs(separation(second, "mars", other) - angle) <= closest
        for other in ("ascendant", "midheaven")
        for angle in (0.0, 180.0)
    )
    assert feature.value(second) is expected
    assert contact(math.nextafter(closest, 0.0)).value(first) is False


@pytest.mark.django_db
def test_a_contact_s_settings_are_kept_in_a_standard_form() -> None:
    feature = Feature(
        name="contact",
        kind="contact",
        settings={
            "point": "mars",
            "others": ["vertex", "sun", "vertex"],
            "angles": [180, 90.0, 0.0, 90],
            "orb": 5,
        },
    )
    feature.full_clean()
    assert feature.settings == {
        "point": "mars",
        "others": ["sun", "vertex"],  # the chart's order, once each
        "angles": [0.0, 90.0, 180.0],  # sorted, once each
        "orb": 5.0,
    }
    assert all(isinstance(angle, float) for angle in feature.settings["angles"])
    assert isinstance(feature.settings["orb"], float)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"point": "chiron"}, "'chiron' is not one of the chart's points"),
        ({"others": ["ascendant", "chiron"]}, "'chiron' is not one of the chart's points"),
        ({"others": []}, "name at least one other point"),
        ({"others": ["mars", "ascendant"]}, "a point can't be in contact with itself"),
        ({"angles": []}, "name at least one aspect angle"),
        ({"angles": [0.0, 200.0]}, "angle 200 is outside 0° to 180°"),
        ({"angles": [-10.0]}, "angle -10 is outside 0° to 180°"),
        ({"orb": -1.0}, "the orb must be a number of degrees, 0 or more"),
        ({"orb": math.inf}, "the orb must be a number of degrees, 0 or more"),
        ({"orb": "ten"}, "the orb must be a number of degrees, 0 or more"),
    ],
)
def test_a_contact_s_settings_are_checked(settings: dict[str, Any], message: str) -> None:
    feature = Feature(
        name="contact", kind="contact", settings={**ON_AN_ANGLE, "orb": 10.0, **settings}
    )
    with pytest.raises(ValidationError, match=re.escape(message)):
        feature.full_clean()


# --- Sign -----------------------------------------------------------------------------------


def test_signs_are_30_degree_steps_from_0_aries() -> None:
    assert SIGNS[0] == "aries" and SIGNS[11] == "pisces" and len(SIGNS) == 12
    assert sign_of(0.0) == "aries"
    assert sign_of(math.nextafter(30.0, 0.0)) == "aries"
    assert sign_of(30.0) == "taurus"
    assert sign_of(180.0) == "libra"
    assert sign_of(math.nextafter(360.0, 0.0)) == "pisces"


@pytest.mark.django_db
def test_a_sign_is_the_point_s_from_its_longitude(run: CalculationRun) -> None:
    for point in POINTS:
        feature = Feature(name="sign", kind="sign", settings={"point": point})
        feature.full_clean()
        for _, chart in charted(run):
            assert feature.value(chart) == SIGNS[int(longitude(chart, point) // 30)], point


@pytest.mark.django_db
def test_a_sign_s_point_is_checked() -> None:
    feature = Feature(name="sign", kind="sign", settings={"point": "chiron"})
    with pytest.raises(ValidationError, match="'chiron' is not one of the chart's points"):
        feature.full_clean()


@pytest.mark.django_db
def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(ValidationError, match="kind 'house' is not one of contact, sign"):
        Feature(name="house", kind="house", settings={}).full_clean()


@pytest.mark.django_db
def test_feature_names_are_unique() -> None:
    Feature.objects.create(name="sun sign", kind="sign", settings={"point": "sun"})
    with pytest.raises(ValidationError):
        Feature(name="sun sign", kind="sign", settings={"point": "moon"}).full_clean()


# --- Adding a feature -----------------------------------------------------------------------


@pytest.mark.django_db
def test_adding_a_feature_starts_by_choosing_its_kind(admin_client: Client) -> None:
    text = page(admin_client, reverse("feature-new"))
    for kind in ("contact", "sign"):
        assert f'href="{reverse("feature-new")}?kind={kind}"' in text
    response = admin_client.get(reverse("feature-new") + "?kind=house")
    assert response.status_code == 400
    assert "kind 'house' is not one of contact, sign" in html.unescape(response.content.decode())


@pytest.mark.django_db
def test_a_kind_s_form_is_a_post_with_a_csrf_token(admin_client: Client) -> None:
    text = page(admin_client, reverse("feature-new") + "?kind=contact")
    form = re.search(
        r'<form[^>]*method="post"[^>]*>(.*?)</form>', text.split("</header>", 1)[1], re.S
    )
    assert form is not None
    for name in ("csrfmiddlewaretoken", "name", "point", "others", "angles", "orb"):
        assert f'name="{name}"' in form.group(1), name
    sign = page(admin_client, reverse("feature-new") + "?kind=sign")
    assert 'name="point"' in sign and 'name="orb"' not in sign


@pytest.mark.django_db
def test_adding_a_contact_feature_opens_its_page(admin_client: Client) -> None:
    response = admin_client.post(reverse("feature-new") + "?kind=contact", CONTACT_FORM)
    feature = Feature.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("feature", args=[feature.pk])
    assert (feature.name, feature.kind) == ("Mars on an angle", "contact")
    assert feature.description == "Ecliptic orb, not Gauquelin's sectors"
    assert feature.settings == {**ON_AN_ANGLE, "orb": 10.0}


@pytest.mark.django_db
def test_adding_a_sign_feature(admin_client: Client) -> None:
    form = {"name": "Sun sign", "description": "", "point": "sun"}
    response = admin_client.post(reverse("feature-new") + "?kind=sign", form)
    feature = Feature.objects.get()
    assert response.status_code == 302
    assert (feature.kind, feature.settings) == ("sign", {"point": "sun"})


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"others": ["mars"]}, "a point can't be in contact with itself"),
        ({"angles": "0, 200"}, "angle 200 is outside 0° to 180°"),
        ({"angles": "square"}, "'square' is neither an angle nor a set name"),
        ({"orb": "-1"}, "the orb must be a number of degrees, 0 or more"),
    ],
)
def test_a_feature_the_form_can_t_save_shows_why(
    admin_client: Client, change: dict[str, Any], message: str
) -> None:
    form = {**CONTACT_FORM, **change}
    response = admin_client.post(reverse("feature-new") + "?kind=contact", form)
    assert response.status_code == 400
    text = html.unescape(response.content.decode())
    assert message in text
    assert re.search(r'name="name"[^>]*value="Mars on an angle"', text)  # kept as entered
    assert not Feature.objects.exists()


@pytest.mark.django_db
def test_a_feature_s_name_must_be_new(admin_client: Client) -> None:
    Feature.objects.create(name="Mars on an angle", kind="sign", settings={"point": "mars"})
    response = admin_client.post(reverse("feature-new") + "?kind=contact", CONTACT_FORM)
    assert response.status_code == 400
    assert Feature.objects.count() == 1


@pytest.mark.django_db
def test_a_feature_without_its_csrf_token_is_refused(django_user_model: Any) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(django_user_model.objects.create_superuser("checker", "", "unused"))
    response = client.post(reverse("feature-new") + "?kind=contact", CONTACT_FORM)
    assert response.status_code == 403
    assert not Feature.objects.exists()


# --- The features' pages --------------------------------------------------------------------


@pytest.mark.django_db
def test_the_features_are_listed_by_name(admin_client: Client) -> None:
    sun = Feature.objects.create(name="Sun sign", kind="sign", settings={"point": "sun"})
    mars = Feature.objects.create(
        name="Mars on an angle", kind="contact", settings={**ON_AN_ANGLE, "orb": 10.0}
    )
    text = page(admin_client, reverse("features"))
    assert re.findall(r'data-feature="(\d+)"', text) == [str(mars.pk), str(sun.pk)]
    for feature in (mars, sun):
        assert f'href="{reverse("feature", args=[feature.pk])}"' in text
    assert f'href="{reverse("feature-new")}"' in text


@pytest.mark.django_db
def test_every_page_links_to_the_features(admin_client: Client) -> None:
    assert f'href="{reverse("features")}"' in page(admin_client, reverse("datasets"))


@pytest.mark.django_db
def test_a_feature_s_page_offers_the_runs_to_show(
    admin_client: Client, run: CalculationRun
) -> None:
    feature = Feature.objects.create(name="Sun sign", kind="sign", settings={"point": "sun"})
    text = page(admin_client, reverse("feature", args=[feature.pk]))
    assert "Sun sign" in text and "sun" in text
    assert f'href="{reverse("feature", args=[feature.pk])}?run={run.pk}"' in text
    assert "data-record" not in text  # no values until a run is chosen


@pytest.mark.django_db
def test_a_contact_feature_s_page_shows_each_record_s_value_for_a_run(
    admin_client: Client, run: CalculationRun
) -> None:
    (_, first), _ = charted(run)
    closest = min(
        abs(separation(first, "mars", other) - angle)
        for other in ("ascendant", "midheaven")
        for angle in (0.0, 180.0)
    )
    feature = Feature.objects.create(
        name="Mars on an angle", kind="contact", settings={**ON_AN_ANGLE, "orb": closest}
    )
    text = page(admin_client, reverse("feature", args=[feature.pk]) + f"?run={run.pk}")
    values = {record.pk: "yes" if feature.value(chart) else "no" for record, chart in charted(run)}
    shown = dict(re.findall(r'data-record="(\d+)"[^>]*data-value="([a-z]+)"', text))
    assert shown == {str(pk): value for pk, value in values.items()}
    yes = sum(value == "yes" for value in values.values())
    counts = re.search(r'class="counts">([^<]*)<', text)
    assert counts is not None and counts.group(1).strip() == f"yes {yes} · no {2 - yes}"
    refused = run.outcomes.get(chart=None).record
    assert f'data-record="{refused.pk}"' not in text and "1 refused" in text


@pytest.mark.django_db
def test_a_sign_feature_s_page_counts_every_sign(admin_client: Client, run: CalculationRun) -> None:
    feature = Feature.objects.create(name="Sun sign", kind="sign", settings={"point": "sun"})
    text = page(admin_client, reverse("feature", args=[feature.pk]) + f"?run={run.pk}")
    signs = [feature.value(chart) for _, chart in charted(run)]
    counts = re.search(r'class="counts">([^<]*)<', text)
    assert counts is not None
    assert counts.group(1).strip() == " · ".join(f"{s} {signs.count(s)}" for s in SIGNS)


@pytest.mark.django_db
def test_a_stored_document_this_engine_can_t_read_shows_why(
    admin_client: Client, run: CalculationRun
) -> None:
    *_, (_, chart) = charted(run)
    StoredChart.objects.filter(pk=chart.calculation_key).update(document="{}")
    feature = Feature.objects.create(name="Sun sign", kind="sign", settings={"point": "sun"})
    response = admin_client.get(reverse("feature", args=[feature.pk]) + f"?run={run.pk}")
    assert response.status_code == 422
    text = html.unescape(response.content.decode())
    assert "InvalidChartDocument" in text and chart.calculation_key in text
    assert "data-record" not in text


@pytest.mark.django_db
def test_an_unknown_feature_or_run_is_not_found(admin_client: Client, run: CalculationRun) -> None:
    feature = Feature.objects.create(name="Sun sign", kind="sign", settings={"point": "sun"})
    assert admin_client.get(reverse("feature", args=[999_999])).status_code == 404
    address = reverse("feature", args=[feature.pk])
    assert admin_client.get(f"{address}?run=999999").status_code == 404
    assert admin_client.get(f"{address}?run=latest").status_code == 400


@pytest.mark.django_db
def test_the_features_need_a_login(client: Client) -> None:
    feature = Feature.objects.create(name="Sun sign", kind="sign", settings={"point": "sun"})
    for address in (
        reverse("features"),
        reverse("feature-new"),
        reverse("feature", args=[feature.pk]),
    ):
        response = client.get(address)
        assert response.status_code == 302 and "login" in response["Location"], address
    response = client.post(reverse("feature-new") + "?kind=contact", CONTACT_FORM)
    assert response.status_code == 302 and Feature.objects.count() == 1
