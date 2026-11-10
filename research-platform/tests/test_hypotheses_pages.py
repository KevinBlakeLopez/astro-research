"""The hypotheses' pages: the list, adding and editing from forms, running, and each result.

- The list shows each hypothesis by name with its latest result, and the number of results
  recorded in all, so how many tests were run is always in view.
- Adding one starts with its analysis, then the analysis's form. The form offers only what
  fits: yes/no features, groups ("profession: athlete", "left-handed: yes", never a number
  variable) and finished runs. Directions are in words.
- A hypothesis's page shows its definition and a Run button, a POST labelled "Run (locks this
  hypothesis)" before the first result and "Run again" after; every run is kept. Before its
  first result it can be edited with the same form; after, it's shown as locked and editing is
  refused.
- Each result shows its labelled table, p-value, verdict and what was left out, and its inputs
  download as CSV, byte for byte as stored.
- A run the analysis refuses shows why (422), and records nothing.

Markup the tests rely on: list rows carry data-hypothesis; each result is a section with
data-result, and its table's cells data-cell="group-yes", "group-no", "rest-yes", "rest-no".
"""

import html
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from astro_research.chart import BirthRecord, ChartSettings
from astro_research.civil_time import UtcTime, ZonedLocalTime
from astro_research.ephemeris import Ephemeris
from astro_research.geo import GeoPosition
from astro_research.measurements import MAJOR
from research_platform import analyses
from research_platform.hypotheses import run_hypothesis
from research_platform.models import (
    AnalysisRun,
    CalculationRun,
    Category,
    Dataset,
    Feature,
    Hypothesis,
    Observation,
    Record,
    Source,
    Subject,
    Variable,
)
from research_platform.runner import run_dataset

LONDON = GeoPosition(latitude=51.5074, longitude=-0.1278)
AMBIGUOUS = BirthRecord(  # refused: 01:30 happens twice as British Summer Time ends
    time=ZonedLocalTime(local=datetime(1987, 10, 25, 1, 30), zone="Europe/London"), place=LONDON
)
DIRECTIONS = {
    "greater": "more common in the group",
    "less": "less common in the group",
    "two-sided": "either way",
}


def birth(hours: int) -> BirthRecord:
    return BirthRecord(
        time=UtcTime(utc=datetime(1990, 3, 21, tzinfo=UTC) + timedelta(hours=hours)),
        place=LONDON,
    )


@pytest.fixture
def profession() -> Variable:
    variable = Variable.objects.create(name="profession", kind=Variable.Kind.CATEGORY)
    for name in ("athlete", "musician"):
        Category.objects.create(variable=variable, name=name)
    return variable


@pytest.fixture
def mars_rising() -> Feature:
    feature = Feature(
        name="Mars rising",
        kind="contact",
        settings={"point": "mars", "others": ["ascendant"], "angles": [0], "orb": 90},
    )
    feature.full_clean()
    feature.save()
    return feature


@pytest.fixture
def study(
    store: Callable[..., Record], source: Source, profession: Variable, eph: Ephemeris
) -> CalculationRun:
    """Five subjects: two athletes, two musicians, one with no profession; and a sixth
    subject's record that the engine refuses."""
    athlete, musician = profession.categories.order_by("pk")
    records = []
    for n in range(5):
        subject = Subject.objects.create(label=f"study-{n}")
        records.append(store(birth(5 * n), subject=subject))
        if n < 4:
            Observation.objects.create(
                variable=profession,
                subject=subject,
                source=source,
                category=athlete if n < 2 else musician,
            )
    records.append(store(AMBIGUOUS, subject=Subject.objects.create(label="study-refused")))
    dataset = Dataset.objects.create(name="study")
    dataset.records.add(*records)
    return run_dataset(dataset, ChartSettings(aspect_angles=MAJOR), eph)


def athletes(profession: Variable) -> Category:
    return profession.categories.get(name="athlete")


def form(
    feature: Feature, profession: Variable, run: CalculationRun, **changes: Any
) -> dict[str, Any]:
    return {
        "name": "Mars rising in athletes",
        "statement": "Athletes are born with Mars rising more often than musicians.",
        "feature": feature.pk,
        "group": f"category-{athletes(profession).pk}",
        "run": run.pk,
        "alternative": "greater",
        "alpha": "0.05",
        **changes,
    }


def registered(feature: Feature, profession: Variable, run: CalculationRun) -> Hypothesis:
    made = Hypothesis(
        name="Mars rising in athletes",
        statement="Athletes are born with Mars rising more often than musicians.",
        feature=feature,
        variable=profession,
        category=athletes(profession),
        run=run,
        analysis="fisher-exact",
        settings={"alternative": "greater", "alpha": 0.05},
    )
    made.full_clean()
    made.save()
    return made


def page(client: Client, address: str) -> str:
    response = client.get(address)
    assert response.status_code == 200, response.content.decode()[:500]
    return html.unescape(response.content.decode())


def select(text: str, name: str) -> str:
    found = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', text, re.S)
    assert found is not None, name
    return found.group(1)


NEW = "hypothesis-new"
FISHER = "?analysis=fisher-exact"


# --- Adding a hypothesis --------------------------------------------------------------------


@pytest.mark.django_db
def test_adding_a_hypothesis_starts_by_choosing_its_analysis(admin_client: Client) -> None:
    text = page(admin_client, reverse(NEW))
    assert f'href="{reverse(NEW)}{FISHER}"' in text and "Fisher's exact test" in text
    response = admin_client.get(reverse(NEW) + "?analysis=t-test")
    assert response.status_code == 400
    assert "analysis 't-test' is not one of fisher-exact" in html.unescape(
        response.content.decode()
    )


@pytest.mark.django_db
def test_the_form_offers_only_what_fits(
    admin_client: Client, mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    sign = Feature.objects.create(name="Sun sign", kind="sign", settings={"point": "sun"})
    left = Variable.objects.create(name="left-handed", kind=Variable.Kind.YES_NO)
    Variable.objects.create(name="height", kind=Variable.Kind.NUMBER)
    failed = CalculationRun.objects.create(
        dataset=study.dataset,
        settings=study.settings,
        engine_version=study.engine_version,
        ephemeris=study.ephemeris,
        status=CalculationRun.Status.FAILED,
        started_at=study.started_at,
    )
    text = page(admin_client, reverse(NEW) + FISHER)
    features = select(text, "feature")
    assert f'value="{mars_rising.pk}"' in features and f'value="{sign.pk}"' not in features
    groups = select(text, "group")
    for category in profession.categories.all():
        assert f'value="category-{category.pk}"' in groups
    assert "profession: athlete" in groups and "profession: musician" in groups
    assert f'value="yes-{left.pk}"' in groups and "left-handed: yes" in groups
    assert "height" not in groups
    runs = select(text, "run")
    assert f'value="{study.pk}"' in runs and f'value="{failed.pk}"' not in runs
    for words in DIRECTIONS.values():
        assert words in text
    form_tag = re.search(
        r'<form[^>]*method="post"[^>]*>(.*?)</form>', text.split("</header>", 1)[1], re.S
    )
    assert form_tag is not None and 'name="csrfmiddlewaretoken"' in form_tag.group(1)


@pytest.mark.django_db
def test_adding_a_hypothesis_opens_its_page(
    admin_client: Client, mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    response = admin_client.post(reverse(NEW) + FISHER, form(mars_rising, profession, study))
    made = Hypothesis.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("hypothesis", args=[made.pk])
    assert (made.feature, made.variable, made.category, made.run) == (
        mars_rising,
        profession,
        athletes(profession),
        study,
    )
    assert (made.analysis, made.settings) == (
        "fisher-exact",
        {"alternative": "greater", "alpha": 0.05},
    )


@pytest.mark.django_db
def test_a_yes_no_group_is_its_variable_s_yes(
    admin_client: Client, mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    left = Variable.objects.create(name="left-handed", kind=Variable.Kind.YES_NO)
    data = form(mars_rising, profession, study, group=f"yes-{left.pk}")
    assert admin_client.post(reverse(NEW) + FISHER, data).status_code == 302
    made = Hypothesis.objects.get()
    assert (made.variable, made.category) == (left, None)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"alpha": "1.5"}, "the significance level must be a number between 0 and 1"),
        ({"alternative": "sideways"}, "Select a valid choice"),
        ({"group": "category-999999"}, "Select a valid choice"),
    ],
)
def test_a_hypothesis_the_form_can_t_save_shows_why(
    admin_client: Client,
    mars_rising: Feature,
    profession: Variable,
    study: CalculationRun,
    change: dict[str, str],
    message: str,
) -> None:
    response = admin_client.post(
        reverse(NEW) + FISHER, form(mars_rising, profession, study, **change)
    )
    assert response.status_code == 400
    text = html.unescape(response.content.decode())
    assert message in text
    assert re.search(r'name="name"[^>]*value="Mars rising in athletes"', text)
    assert not Hypothesis.objects.exists()


@pytest.mark.django_db
def test_a_sign_feature_can_t_be_slipped_in(
    admin_client: Client, profession: Variable, study: CalculationRun
) -> None:
    sign = Feature.objects.create(name="Sun sign", kind="sign", settings={"point": "sun"})
    response = admin_client.post(reverse(NEW) + FISHER, form(sign, profession, study))
    assert response.status_code == 400 and not Hypothesis.objects.exists()


@pytest.mark.django_db
def test_a_hypothesis_s_name_must_be_new(
    admin_client: Client, mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    registered(mars_rising, profession, study)
    response = admin_client.post(reverse(NEW) + FISHER, form(mars_rising, profession, study))
    assert response.status_code == 400 and Hypothesis.objects.count() == 1


@pytest.mark.django_db
def test_adding_without_the_csrf_token_is_refused(
    django_user_model: Any, mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(django_user_model.objects.create_superuser("checker", "", "unused"))
    response = client.post(reverse(NEW) + FISHER, form(mars_rising, profession, study))
    assert response.status_code == 403 and not Hypothesis.objects.exists()


# --- A hypothesis's page and running it -----------------------------------------------------


@pytest.mark.django_db
def test_a_hypothesis_s_page_shows_its_definition_and_run_button(
    admin_client: Client, mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    made = registered(mars_rising, profession, study)
    text = page(admin_client, reverse("hypothesis", args=[made.pk]))
    assert made.statement in text and "profession: athlete" in text
    assert DIRECTIONS["greater"] in text and "0.05" in text and "Fisher's exact test" in text
    for address in (
        reverse("feature", args=[mars_rising.pk]),
        reverse("run", args=[study.pk]),
        reverse("hypothesis-edit", args=[made.pk]),
    ):
        assert f'href="{address}"' in text, address
    action = reverse("hypothesis-run", args=[made.pk])
    button = re.search(
        rf'<form[^>]*method="post"[^>]*action="{action}"[^>]*>(.*?)</form>', text, re.S
    )
    assert button is not None
    assert 'name="csrfmiddlewaretoken"' in button.group(1)
    assert "Run (locks this hypothesis)" in button.group(1)
    assert "data-result" not in text


@pytest.mark.django_db
def test_running_records_a_result_and_shows_it(
    admin_client: Client, mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    made = registered(mars_rising, profession, study)
    response = admin_client.post(reverse("hypothesis-run", args=[made.pk]))
    result = AnalysisRun.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("hypothesis", args=[made.pk])
    text = page(admin_client, reverse("hypothesis", args=[made.pk]))
    section = re.search(rf'<section[^>]*data-result="{result.pk}".*?</section>', text, re.S)
    assert section is not None
    shown = dict(re.findall(r'data-cell="([a-z-]+)">\s*(\d+)', section.group(0)))
    (gy, gn), (ry, rn) = result.result["table"]
    assert shown == {
        "group-yes": str(gy),
        "group-no": str(gn),
        "rest-yes": str(ry),
        "rest-no": str(rn),
    }
    p = Fraction(result.result["p_value"])
    assert f"{float(p):.4g}" in section.group(0) and f"({p})" in section.group(0)
    verdict = "at or below 0.05" if result.result["below_alpha"] else "above 0.05"
    assert verdict in section.group(0)
    assert "1 refused" in section.group(0) and "1 with no value" in section.group(0)
    assert result.analysis_sha256[:12] in section.group(0)
    assert f'href="{reverse("result-inputs-csv", args=[result.pk])}"' in section.group(0)
    assert "locked" in text and f'href="{reverse("hypothesis-edit", args=[made.pk])}"' not in text


@pytest.mark.django_db
def test_every_result_is_shown_newest_first(
    admin_client: Client, mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    made = registered(mars_rising, profession, study)
    first, second = run_hypothesis(made), run_hypothesis(made)
    text = page(admin_client, reverse("hypothesis", args=[made.pk]))
    assert re.findall(r'data-result="(\d+)"', text) == [str(second.pk), str(first.pk)]
    assert "Run (locks this hypothesis)" not in text and "Run again" in text


@pytest.mark.django_db
def test_running_is_a_post_with_a_csrf_check(
    admin_client: Client,
    django_user_model: Any,
    mars_rising: Feature,
    profession: Variable,
    study: CalculationRun,
) -> None:
    made = registered(mars_rising, profession, study)
    assert admin_client.get(reverse("hypothesis-run", args=[made.pk])).status_code == 405
    client = Client(enforce_csrf_checks=True)
    client.force_login(django_user_model.objects.create_superuser("checker", "", "unused"))
    assert client.post(reverse("hypothesis-run", args=[made.pk])).status_code == 403
    assert not AnalysisRun.objects.exists()


@pytest.mark.django_db
def test_a_run_the_analysis_refuses_shows_why_and_records_nothing(
    admin_client: Client,
    store: Callable[..., Record],
    mars_rising: Feature,
    profession: Variable,
    eph: Ephemeris,
) -> None:
    twice = Subject.objects.create(label="twice")
    dataset = Dataset.objects.create(name="doubled")
    dataset.records.add(store(birth(0), subject=twice), store(birth(5), subject=twice))
    run = run_dataset(dataset, ChartSettings(aspect_angles=MAJOR), eph)
    made = registered(mars_rising, profession, run)
    response = admin_client.post(reverse("hypothesis-run", args=[made.pk]))
    assert response.status_code == 422
    assert "subject 'twice' has 2 records" in html.unescape(response.content.decode())
    assert not AnalysisRun.objects.exists()


# --- Editing --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_hypothesis_without_results_can_be_edited(
    admin_client: Client, mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    made = registered(mars_rising, profession, study)
    text = page(admin_client, reverse("hypothesis-edit", args=[made.pk]))
    assert re.search(r'name="name"[^>]*value="Mars rising in athletes"', text)
    data = form(mars_rising, profession, study, alternative="two-sided", statement="Changed")
    response = admin_client.post(reverse("hypothesis-edit", args=[made.pk]), data)
    assert response.status_code == 302
    made.refresh_from_db()
    assert made.statement == "Changed" and made.settings["alternative"] == "two-sided"


@pytest.mark.django_db
def test_a_hypothesis_with_a_result_can_t_be_edited(
    admin_client: Client, mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    made = registered(mars_rising, profession, study)
    run_hypothesis(made)
    message = "Mars rising in athletes has results, so it can't change"
    response = admin_client.get(reverse("hypothesis-edit", args=[made.pk]))
    assert response.status_code == 400 and message in html.unescape(response.content.decode())
    data = form(mars_rising, profession, study, statement="Changed")
    response = admin_client.post(reverse("hypothesis-edit", args=[made.pk]), data)
    assert response.status_code == 400 and message in html.unescape(response.content.decode())
    made.refresh_from_db()
    assert made.statement != "Changed"


# --- The list, the inputs, and getting there ------------------------------------------------


@pytest.mark.django_db
def test_the_hypotheses_are_listed_with_their_latest_result_and_the_total(
    admin_client: Client, mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    made = registered(mars_rising, profession, study)
    other = Hypothesis(
        name="A musicians' hypothesis",
        statement="Musicians",
        feature=mars_rising,
        variable=profession,
        category=profession.categories.get(name="musician"),
        run=study,
        analysis="fisher-exact",
        settings={"alternative": "two-sided", "alpha": 0.01},
    )
    other.full_clean()
    other.save()
    first = run_hypothesis(made)
    observation = Observation.objects.get(subject__label="study-0")  # so the results differ
    observation.category = profession.categories.get(name="musician")
    observation.save()
    latest = run_hypothesis(made)
    run_hypothesis(other)
    summary = analyses.analysis("fisher-exact").summarize
    assert summary(first.result, made.settings) != summary(latest.result, made.settings)
    text = page(admin_client, reverse("hypotheses"))
    assert re.findall(r'data-hypothesis="(\d+)"', text) == [str(other.pk), str(made.pk)]  # by name
    row = re.search(rf'<tr[^>]*data-hypothesis="{made.pk}".*?</tr>', text, re.S)
    assert row is not None
    assert "profession: athlete" in row.group(0)
    assert summary(latest.result, made.settings) in row.group(0)
    assert summary(first.result, made.settings) not in row.group(0)
    assert re.search(r">\s*2\s*<", row.group(0))  # its results
    assert "3 results recorded in all" in text  # two of one hypothesis, one of the other
    assert f'href="{reverse(NEW)}"' in text


@pytest.mark.django_db
def test_a_result_s_inputs_download_as_csv_as_stored(
    admin_client: Client, mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    made = registered(mars_rising, profession, study)
    stored = run_hypothesis(made)
    result = AnalysisRun.objects.create(  # every combination, and a label CSV must quote
        hypothesis=made,
        inputs=[
            {"record": 1, "subject": "yes, out", "feature": True, "in_group": False},
            {"record": 2, "subject": "no, in", "feature": False, "in_group": True},
            {"record": 3, "subject": "both", "feature": True, "in_group": True},
        ],
        excluded=stored.excluded,
        result=stored.result,
        analysis_sha256=stored.analysis_sha256,
        engine_version=stored.engine_version,
    )
    response = admin_client.get(reverse("result-inputs-csv", args=[result.pk]))
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert f'filename="result-{result.pk}-inputs.csv"' in response["Content-Disposition"]
    assert response.content.decode() == (
        "record_id,subject,feature,in_group\r\n"
        '1,"yes, out",true,false\r\n'
        '2,"no, in",false,true\r\n'
        "3,both,true,true\r\n"
    )


@pytest.mark.django_db
def test_features_and_runs_link_to_their_hypotheses(
    admin_client: Client, mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    made = registered(mars_rising, profession, study)
    link = f'href="{reverse("hypothesis", args=[made.pk])}"'
    assert link in page(admin_client, reverse("feature", args=[mars_rising.pk]))
    assert link in page(admin_client, reverse("run", args=[study.pk]))
    assert f'href="{reverse("hypotheses")}"' in page(admin_client, reverse("datasets"))


@pytest.mark.django_db
def test_unknown_hypotheses_and_results_are_not_found(admin_client: Client) -> None:
    for name in ("hypothesis", "hypothesis-edit", "result-inputs-csv"):
        assert admin_client.get(reverse(name, args=[999_999])).status_code == 404, name
    assert admin_client.post(reverse("hypothesis-run", args=[999_999])).status_code == 404


@pytest.mark.django_db
def test_the_hypotheses_need_a_login(
    client: Client, mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    made = registered(mars_rising, profession, study)
    result = run_hypothesis(made)
    for address in (
        reverse("hypotheses"),
        reverse(NEW),
        reverse("hypothesis", args=[made.pk]),
        reverse("hypothesis-edit", args=[made.pk]),
        reverse("result-inputs-csv", args=[result.pk]),
    ):
        response = client.get(address)
        assert response.status_code == 302 and "login" in response["Location"], address
    response = client.post(reverse("hypothesis-run", args=[made.pk]))
    assert response.status_code == 302 and AnalysisRun.objects.count() == 1
