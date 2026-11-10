"""What's known about each subject: variables and their observations.

A `Variable` is a category, a number or yes/no. A category variable lists its `Category`s, and a
number variable can name its unit. An `Observation` is one subject's value of one variable, from
a cited source, checked against its variable when it's validated: one of its categories, a
finite number, or yes or no, and nothing else. A subject has at most one value per variable;
a subject without one is missing, and nothing fills it in.

Whether a variable is an outcome or something to control for is the hypothesis's to say, not
the variable's. A category in use can't be deleted, and a variable with values can't change its
kind, so no value is left meaning something it no longer can.

A dataset's page shows each variable's coverage among the dataset's subjects (not its records:
a dataset may hold several records of one subject). Every value here is synthetic.

Markup the tests rely on: coverage rows carry data-variable with the variable's id, and their
cell of the form "2 of 3" has class "coverage".
"""

import html
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import Client
from django.urls import reverse

from astro_research.chart import BirthRecord
from astro_research.civil_time import UtcTime
from astro_research.geo import GeoPosition
from research_platform.models import (
    Category,
    Dataset,
    Observation,
    Record,
    Source,
    Subject,
    Variable,
)

PLACE = GeoPosition(latitude=48.8566, longitude=2.3522)


def birth(hour: int) -> BirthRecord:
    return BirthRecord(time=UtcTime(utc=datetime(1970, 1, 1, hour, tzinfo=UTC)), place=PLACE)


@pytest.fixture
def profession() -> Variable:
    variable = Variable.objects.create(name="profession", kind=Variable.Kind.CATEGORY)
    for name in ("athlete", "musician", "painter"):
        Category.objects.create(variable=variable, name=name)
    return variable


@pytest.fixture
def height() -> Variable:
    return Variable.objects.create(name="height", kind=Variable.Kind.NUMBER, unit="cm")


@pytest.fixture
def left_handed() -> Variable:
    return Variable.objects.create(name="left-handed", kind=Variable.Kind.YES_NO)


def observed(variable: Variable, subject: Subject, source: Source, **value: object) -> Observation:
    """An unsaved observation of `variable` for `subject`, with `value` in its fields."""
    return Observation(variable=variable, subject=subject, source=source, **value)


def category(variable: Variable, name: str) -> Category:
    return variable.categories.get(name=name)


# --- Values and their kinds -----------------------------------------------------------------


@pytest.mark.django_db
def test_each_kind_s_value_reads_back(
    profession: Variable, height: Variable, left_handed: Variable, subject: Subject, source: Source
) -> None:
    values = [
        observed(profession, subject, source, category=category(profession, "athlete")),
        observed(height, subject, source, number=170.5),
        observed(left_handed, subject, source, yes_no=False),
    ]
    for observation in values:
        observation.full_clean()
        observation.save()
    read = {o.variable.name: o.value for o in Observation.objects.select_related("category")}
    assert read == {"profession": "athlete", "height": 170.5, "left-handed": False}


@pytest.mark.django_db
def test_a_category_must_be_one_of_its_variable_s(
    profession: Variable, subject: Subject, source: Source
) -> None:
    instrument = Variable.objects.create(name="instrument", kind=Variable.Kind.CATEGORY)
    violin = Category.objects.create(variable=instrument, name="violin")
    with pytest.raises(ValidationError, match="'violin' is not one of profession's categories"):
        observed(profession, subject, source, category=violin).full_clean()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("profession", {"number": 5.0}, "profession is a category variable"),
        ("profession", {}, "profession is a category variable"),
        ("height", {"yes_no": True}, "height is a number variable"),
        ("height", {}, "height is a number variable"),
        ("left-handed", {"number": 1.0}, "left-handed is a yes/no variable"),
        ("left-handed", {}, "left-handed is a yes/no variable"),
    ],
)
def test_a_value_must_be_of_its_variable_s_kind_and_nothing_else(
    profession: Variable,
    height: Variable,
    left_handed: Variable,
    subject: Subject,
    source: Source,
    name: str,
    value: dict[str, object],
    message: str,
) -> None:
    variable = Variable.objects.get(name=name)
    with pytest.raises(ValidationError, match=message):
        observed(variable, subject, source, **value).full_clean()


@pytest.mark.django_db
def test_a_category_value_can_t_also_hold_a_number(
    profession: Variable, subject: Subject, source: Source
) -> None:
    athlete = category(profession, "athlete")
    with pytest.raises(ValidationError, match="profession is a category variable"):
        observed(profession, subject, source, category=athlete, number=1.0).full_clean()


@pytest.mark.django_db
def test_the_database_holds_exactly_one_value_per_observation(
    profession: Variable, subject: Subject, source: Source
) -> None:
    athlete = category(profession, "athlete")
    for value in ({}, {"category": athlete, "number": 1.0}):
        with transaction.atomic(), pytest.raises(IntegrityError, match="one_value"):
            observed(profession, subject, source, **value).save()  # skipping validation


@pytest.mark.django_db
@pytest.mark.parametrize("number", [math.nan, math.inf, -math.inf])
def test_a_number_must_be_finite(
    height: Variable, subject: Subject, source: Source, number: float
) -> None:
    with pytest.raises(ValidationError, match="a number must be finite"):
        observed(height, subject, source, number=number).full_clean()


# --- One value per subject, from a source ---------------------------------------------------


@pytest.mark.django_db
def test_a_subject_has_one_value_per_variable(
    height: Variable, subject: Subject, source: Source
) -> None:
    observed(height, subject, source, number=170.5).save()
    second = observed(height, subject, Source.objects.create(citation="Another"), number=171.0)
    with pytest.raises(ValidationError):
        second.full_clean()
    with transaction.atomic(), pytest.raises(IntegrityError, match="one_value_per_subject"):
        second.save()
    other = Subject.objects.create(label="subject-2")
    observed(height, other, source, number=180.0).full_clean()  # another subject is fine


@pytest.mark.django_db
def test_a_value_needs_a_source(height: Variable, subject: Subject) -> None:
    with pytest.raises(ValidationError, match="source"):
        Observation(variable=height, subject=subject, number=170.5).full_clean()


# --- Changing a variable --------------------------------------------------------------------


@pytest.mark.django_db
def test_a_category_in_use_can_t_be_deleted(
    profession: Variable, subject: Subject, source: Source
) -> None:
    observed(profession, subject, source, category=category(profession, "athlete")).save()
    with pytest.raises(ProtectedError):
        category(profession, "athlete").delete()
    category(profession, "painter").delete()  # unused


@pytest.mark.django_db
def test_a_variable_with_values_can_t_change_its_kind(
    height: Variable, left_handed: Variable, subject: Subject, source: Source
) -> None:
    observed(height, subject, source, number=170.5).save()
    height.kind = Variable.Kind.CATEGORY
    with pytest.raises(ValidationError, match="height has values, so its kind can't change"):
        height.full_clean()
    left_handed.kind = Variable.Kind.NUMBER  # no values yet
    left_handed.full_clean()


@pytest.mark.django_db
def test_a_variable_with_categories_stays_a_category_variable(profession: Variable) -> None:
    profession.kind = Variable.Kind.NUMBER
    with pytest.raises(ValidationError, match="profession has categories"):
        profession.full_clean()
    profession.categories.all().delete()
    profession.full_clean()


@pytest.mark.django_db
def test_only_a_category_variable_has_categories(height: Variable, profession: Variable) -> None:
    with pytest.raises(ValidationError, match="only a category variable has categories"):
        Category(variable=height, name="tall").full_clean()
    with pytest.raises(ValidationError):
        Category(variable=profession, name="athlete").full_clean()  # already one of them
    Category(variable=profession, name="writer").full_clean()


@pytest.mark.django_db
def test_variable_names_are_unique() -> None:
    Variable.objects.create(name="height", kind=Variable.Kind.NUMBER)
    with pytest.raises(ValidationError):
        Variable(name="height", kind=Variable.Kind.NUMBER).full_clean()


# --- The admin ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_variable_s_admin_page_lists_its_values(
    admin_client: Client, profession: Variable, subject: Subject, source: Source
) -> None:
    observed(profession, subject, source, category=category(profession, "musician")).save()
    response = admin_client.get(f"/admin/research_platform/variable/{profession.pk}/change/")
    assert response.status_code == 200
    text = html.unescape(response.content.decode())
    assert "subject-1" in text and "musician" in text and "painter" in text


@pytest.mark.django_db
def test_a_subject_s_admin_page_shows_its_values(
    admin_client: Client, height: Variable, subject: Subject, source: Source
) -> None:
    observed(height, subject, source, number=170.5).save()
    response = admin_client.get(f"/admin/research_platform/subject/{subject.pk}/change/")
    assert response.status_code == 200
    text = html.unescape(response.content.decode())
    assert "height" in text and "170.5" in text


@pytest.mark.django_db
def test_the_admin_refuses_a_value_of_the_wrong_kind(
    admin_client: Client, profession: Variable, subject: Subject, source: Source
) -> None:
    response = admin_client.post(
        "/admin/research_platform/observation/add/",
        {
            "variable": profession.pk,
            "subject": subject.pk,
            "source": source.pk,
            "category": "",
            "number": "5",
            "yes_no": "unknown",
            "notes": "",
        },
    )
    assert response.status_code == 200  # the form again, not a redirect
    assert "profession is a category variable" in html.unescape(response.content.decode())
    assert not Observation.objects.exists()


# --- A dataset's coverage -------------------------------------------------------------------


@pytest.mark.django_db
def test_a_dataset_s_page_shows_each_variable_s_coverage_among_its_subjects(
    admin_client: Client,
    store: Callable[..., Record],
    subject: Subject,
    source: Source,
    profession: Variable,
    height: Variable,
    left_handed: Variable,
) -> None:
    second, third, outside = (Subject.objects.create(label=f"subject-{n}") for n in (2, 3, 4))
    records = [
        store(birth(1)),
        store(birth(2), subject=second),
        store(birth(3), subject=second),  # a second record of one subject
        store(birth(4), subject=third),
    ]
    dataset = Dataset.objects.create(name="sample")
    dataset.records.add(*records)
    unused = Variable.objects.create(name="unused", kind=Variable.Kind.YES_NO)
    for who, name in ((subject, "athlete"), (second, "athlete"), (outside, "musician")):
        observed(profession, who, source, category=category(profession, name)).save()
    observed(height, subject, source, number=170.5).save()
    observed(height, third, source, number=182.0).save()
    observed(height, outside, source, number=150.0).save()  # not in the dataset
    observed(left_handed, second, source, yes_no=True).save()
    observed(left_handed, third, source, yes_no=False).save()

    response = admin_client.get(reverse("dataset", args=[dataset.pk]))
    assert response.status_code == 200
    text = html.unescape(response.content.decode())
    rows = re.findall(r'data-variable="(\d+)"', text)
    assert rows == [str(v.pk) for v in (height, left_handed, profession, unused)]  # by name

    def row(variable: Variable) -> str:
        found = re.search(rf'<tr[^>]*data-variable="{variable.pk}".*?</tr>', text, re.S)
        assert found is not None
        return found.group(0)

    def coverage(variable: Variable) -> str:
        found = re.search(r'class="coverage">([^<]*)<', row(variable))
        assert found is not None
        return found.group(1).strip()

    assert coverage(profession) == "2 of 3"
    assert "athlete 2 · musician 0 · painter 0" in row(profession)
    assert coverage(height) == "2 of 3"
    assert "170.5 to 182.0 cm" in row(height)
    assert coverage(left_handed) == "2 of 3"
    assert "yes 1 · no 1" in row(left_handed)
    assert coverage(unused) == "0 of 3"
