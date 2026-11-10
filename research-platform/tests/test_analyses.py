"""Hypotheses, the analysis registry, and every result recorded with the inputs it used.

A `Hypothesis` names a yes/no feature, an outcome (a variable and the group: one of its
categories, or yes), a finished calculation run, an analysis from the registry and its settings.
The first analysis is Fisher's exact test on the 2x2 table of group x feature, rows (group,
rest) and columns (feature yes, feature no). Its settings are the direction (greater: the
feature more common in the group; less; or two-sided) and the significance level, with no
defaults. Its p-value is exact, a fraction, from `math.comb`; a result is at or below the level
when p <= alpha, compared exactly.

Running a hypothesis records an `AnalysisRun`: its inputs (each subject's record, feature value
and whether it's in the group), what was left out and why (refused records, subjects with no
value of the variable), the table and p-value, the SHA-256 of the analysis code's file and the
engine version. A dataset with two records of one subject is refused and nothing is recorded.
A hypothesis is locked once it has a result, and so is its feature; both can't be deleted.
Every value here is synthetic.

References: Fisher's tea-tasting table [[3, 1], [1, 3]] gives 17/70 one-sided and 17/35 two-sided
(Fisher, The Design of Experiments, 1935); the dieting table [[1, 9], [11, 3]] gives 0.002759
two-sided, as R's fisher.test does; and small tables are checked against counting every way
the group could have been drawn.
"""

import hashlib
import io
import itertools
import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db.models import ProtectedError

from astro_research.chart import ENGINE_VERSION, BirthRecord, Chart, ChartSettings
from astro_research.civil_time import UtcTime, ZonedLocalTime
from astro_research.ephemeris import Ephemeris
from astro_research.geo import GeoPosition
from astro_research.measurements import MAJOR
from research_platform import analyses
from research_platform.analyses import fisher_exact, odds_ratio
from research_platform.hypotheses import AnalysisRefused, run_hypothesis
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
AMBIGUOUS = BirthRecord(  # 01:30 happens twice as British Summer Time ends: refused
    time=ZonedLocalTime(local=datetime(1987, 10, 25, 1, 30), zone="Europe/London"), place=LONDON
)
FISHER = {"alternative": "greater", "alpha": 0.05}


def birth(hours: int) -> BirthRecord:
    """Every 3 hours the Ascendant moves about 45 degrees, so the feature below changes."""
    when = datetime(1990, 3, 21, tzinfo=UTC) + timedelta(hours=hours)
    return BirthRecord(time=UtcTime(utc=when), place=LONDON)


@pytest.fixture
def profession() -> Variable:
    variable = Variable.objects.create(name="profession", kind=Variable.Kind.CATEGORY)
    for name in ("athlete", "musician"):
        Category.objects.create(variable=variable, name=name)
    return variable


@pytest.fixture
def mars_rising() -> Feature:
    """Mars within 90 degrees of the Ascendant: yes for about half of a day's births."""
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
    """Eight births of eight subjects: four athletes, three musicians and one with no
    profession; and a ninth subject's record that the engine refuses."""
    athlete, musician = profession.categories.order_by("pk")
    records = []
    for n in range(8):
        subject = Subject.objects.create(label=f"study-{n}")
        records.append(store(birth(3 * n), subject=subject))
        if n < 7:
            Observation.objects.create(
                variable=profession,
                subject=subject,
                source=source,
                category=athlete if n < 4 else musician,
            )
    records.append(store(AMBIGUOUS, subject=Subject.objects.create(label="study-refused")))
    dataset = Dataset.objects.create(name="study")
    dataset.records.add(*records)
    return run_dataset(dataset, ChartSettings(aspect_angles=MAJOR), eph)


def hypothesis(
    feature: Feature, profession: Variable, run: CalculationRun, **changes: object
) -> Hypothesis:
    fields: dict[str, object] = {
        "name": "Mars rising in athletes",
        "statement": "Athletes are born with Mars rising more often than musicians.",
        "feature": feature,
        "variable": profession,
        "run": run,
        "analysis": "fisher-exact",
        "settings": FISHER,
        **changes,
    }
    if "category" not in changes:
        fields["category"] = profession.categories.get(name="athlete")
    return Hypothesis(**fields)


def saved(*args: object, **changes: object) -> Hypothesis:
    made = hypothesis(*args, **changes)  # type: ignore[arg-type]
    made.full_clean()
    made.save()
    return made


def charts(run: CalculationRun) -> list[tuple[Record, Chart]]:
    outcomes = run.outcomes.select_related("record__subject", "chart").order_by("record__pk")
    return [(o.record, o.chart.chart()) for o in outcomes if o.chart is not None]


# --- Fisher's exact test --------------------------------------------------------------------


def test_fisher_s_tea_tasting() -> None:
    table = ((3, 1), (1, 3))
    assert fisher_exact(table, "greater") == Fraction(17, 70)
    assert fisher_exact(table, "less") == Fraction(69, 70)
    assert fisher_exact(table, "two-sided") == Fraction(17, 35)


def test_the_dieting_example_as_r_gives_it() -> None:
    assert round(float(fisher_exact(((1, 9), (11, 3)), "two-sided")), 6) == 0.002759


def _counted(table: tuple[tuple[int, int], tuple[int, int]], alternative: str) -> Fraction:
    """The p-value by drawing the group every possible way from subjects with and without the
    feature, and counting the draws as or more extreme than the table."""
    (a, b), (c, d) = table
    subjects = [True] * (a + c) + [False] * (b + d)
    draws = [
        sum(subjects[i] for i in group)
        for group in itertools.combinations(range(len(subjects)), a + b)
    ]
    ways = {x: draws.count(x) for x in set(draws)}
    if alternative == "greater":
        extreme = [x for x in ways if x >= a]
    elif alternative == "less":
        extreme = [x for x in ways if x <= a]
    else:
        extreme = [x for x in ways if ways[x] <= ways[a]]
    return Fraction(sum(ways[x] for x in extreme), len(draws))


def test_fisher_matches_counting_every_draw_for_small_tables() -> None:
    checked = 0
    for a, b, c, d in itertools.product(range(4), repeat=4):
        if a + b == 0 or a + b + c + d > 9:
            continue
        table = ((a, b), (c, d))
        for alternative in ("greater", "less", "two-sided"):
            assert fisher_exact(table, alternative) == _counted(table, alternative), (
                table,
                alternative,
            )
            checked += 1
    assert checked > 300


def test_the_odds_ratio() -> None:
    assert odds_ratio(((3, 1), (1, 3))) == 9.0
    assert odds_ratio(((2, 0), (1, 3))) == math.inf
    assert odds_ratio(((0, 2), (1, 3))) == 0.0
    assert odds_ratio(((0, 0), (1, 3))) is None  # 0/0: undefined


@pytest.mark.parametrize(("alpha", "below"), [(0.3, True), (0.29, False)])
def test_a_p_value_equal_to_the_level_is_at_or_below_it(alpha: float, below: bool) -> None:
    # ((0, 2), (2, 1)) gives exactly 3/10 for "less"; 0.3 as a binary float is a little under
    # 3/10, so the level is read as written.
    inputs = [
        *({"in_group": True, "feature": False} for _ in range(2)),
        *({"in_group": False, "feature": True} for _ in range(2)),
        {"in_group": False, "feature": False},
    ]
    result = analyses.analysis("fisher-exact").run(inputs, {"alternative": "less", "alpha": alpha})
    assert result == {"table": [[0, 2], [2, 1]], "p_value": "3/10", "below_alpha": below}


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"alpha": 0.05}, "alternative None is not one of greater, less, two-sided"),
        (
            {"alternative": "more", "alpha": 0.05},
            "alternative 'more' is not one of greater, less, two-sided",
        ),
        ({"alternative": "greater"}, "the significance level must be a number between 0 and 1"),
        (
            {"alternative": "greater", "alpha": 0},
            "the significance level must be a number between 0 and 1",
        ),
        (
            {"alternative": "greater", "alpha": 1},
            "the significance level must be a number between 0 and 1",
        ),
        (
            {"alternative": "greater", "alpha": "0.05"},
            "the significance level must be a number between 0 and 1",
        ),
        (
            {"alternative": "greater", "alpha": math.nan},
            "the significance level must be a number between 0 and 1",
        ),
    ],
)
def test_a_fisher_test_s_settings_are_checked(settings: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        analyses.analysis("fisher-exact").check(settings)


def test_an_unknown_analysis_is_refused() -> None:
    with pytest.raises(ValidationError, match="analysis 't-test' is not one of fisher-exact"):
        analyses.analysis("t-test")


# --- A hypothesis ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_hypothesis_s_settings_are_kept_as_checked(
    mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    made = hypothesis(
        mars_rising, profession, study, settings={"alpha": 0.05, "alternative": "greater"}
    )
    made.full_clean()
    assert made.settings == {"alternative": "greater", "alpha": 0.05}


@pytest.mark.django_db
def test_fisher_needs_a_yes_no_feature(profession: Variable, study: CalculationRun) -> None:
    sign = Feature.objects.create(name="Sun sign", kind="sign", settings={"point": "sun"})
    with pytest.raises(
        ValidationError, match="fisher-exact needs a yes/no feature; Sun sign is a sign feature"
    ):
        hypothesis(sign, profession, study).full_clean()


@pytest.mark.django_db
def test_a_hypothesis_s_group_must_fit_its_variable(
    mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    with pytest.raises(ValidationError, match="name which of profession's categories is the group"):
        hypothesis(mars_rising, profession, study, category=None).full_clean()
    other = Variable.objects.create(name="instrument", kind=Variable.Kind.CATEGORY)
    violin = Category.objects.create(variable=other, name="violin")
    with pytest.raises(ValidationError, match="'violin' is not one of profession's categories"):
        hypothesis(mars_rising, profession, study, category=violin).full_clean()
    left = Variable.objects.create(name="left-handed", kind=Variable.Kind.YES_NO)
    with pytest.raises(
        ValidationError, match="its group is the subjects with yes; name no category"
    ):
        hypothesis(mars_rising, left, study, category=violin).full_clean()
    hypothesis(mars_rising, left, study, category=None).full_clean()
    height = Variable.objects.create(name="height", kind=Variable.Kind.NUMBER)
    with pytest.raises(
        ValidationError,
        match="fisher-exact needs a category or yes/no variable; height is a number variable",
    ):
        hypothesis(mars_rising, height, study, category=None).full_clean()


@pytest.mark.django_db
def test_a_hypothesis_needs_a_finished_run(
    mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    failed = CalculationRun.objects.create(
        dataset=study.dataset,
        settings=study.settings,
        engine_version=study.engine_version,
        ephemeris=study.ephemeris,
        status=CalculationRun.Status.FAILED,
        started_at=study.started_at,
    )
    with pytest.raises(ValidationError, match=f"run {failed.pk} is failed, not finished"):
        hypothesis(mars_rising, profession, failed).full_clean()


@pytest.mark.django_db
def test_hypothesis_names_are_unique(
    mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    saved(mars_rising, profession, study)
    with pytest.raises(ValidationError):
        hypothesis(mars_rising, profession, study).full_clean()


# --- Running a hypothesis -------------------------------------------------------------------


@pytest.mark.django_db
def test_running_a_hypothesis_records_its_inputs_and_result(
    mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    tested = saved(mars_rising, profession, study)
    unrelated = Variable.objects.create(name="left-handed", kind=Variable.Kind.YES_NO)
    Observation.objects.create(  # another variable's value doesn't count as a profession
        variable=unrelated,
        subject=Subject.objects.get(label="study-7"),
        source=Source.objects.get(),
        yes_no=True,
    )
    result = run_hypothesis(tested)

    groups = {
        o.subject_id: o.category.name
        for o in Observation.objects.filter(variable=profession).select_related("category")
        if o.category is not None
    }
    expected_inputs = [
        {
            "record": record.pk,
            "subject": record.subject.label,
            "feature": mars_rising.value(chart),
            "in_group": groups[record.subject_id] == "athlete",
        }
        for record, chart in charts(study)
        if record.subject_id in groups
    ]
    assert result.inputs == expected_inputs
    refused = study.outcomes.get(chart=None).record
    missing = Record.objects.get(subject__label="study-7")
    assert result.excluded == {"refused": [refused.pk], "missing": [missing.pk]}

    def count(in_group: bool, feature: bool) -> int:
        return sum(
            row["in_group"] is in_group and row["feature"] is feature for row in expected_inputs
        )

    table = ((count(True, True), count(True, False)), (count(False, True), count(False, False)))
    assert result.result["table"] == [list(row) for row in table]
    assert sum(map(sum, table)) == 7 and table[0][0] + table[0][1] == 4
    p = fisher_exact(table, "greater")
    assert result.result["p_value"] == str(p)
    assert result.result["below_alpha"] is (p <= Fraction("0.05"))
    code = Path(analyses.__file__).read_bytes()
    assert result.analysis_sha256 == hashlib.sha256(code).hexdigest()
    assert result.engine_version == ENGINE_VERSION
    assert result.hypothesis == tested and AnalysisRun.objects.count() == 1


@pytest.mark.django_db
def test_a_result_can_be_worked_out_again_from_its_inputs(
    mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    result = run_hypothesis(saved(mars_rising, profession, study))
    rows = [(row["in_group"], row["feature"]) for row in result.inputs]
    table = (
        (rows.count((True, True)), rows.count((True, False))),
        (rows.count((False, True)), rows.count((False, False))),
    )
    assert result.result["p_value"] == str(fisher_exact(table, "greater"))


@pytest.mark.django_db
def test_a_yes_no_variable_s_group_is_the_subjects_with_yes(
    mars_rising: Feature, profession: Variable, study: CalculationRun, source: Source
) -> None:
    left = Variable.objects.create(name="left-handed", kind=Variable.Kind.YES_NO)
    subjects = Subject.objects.filter(label__startswith="study-").exclude(label="study-refused")
    for n, subject in enumerate(subjects.order_by("pk")):
        Observation.objects.create(variable=left, subject=subject, source=source, yes_no=n % 2 == 0)
    result = run_hypothesis(saved(mars_rising, left, study, category=None))
    lefties = {o.subject.label for o in Observation.objects.filter(variable=left, yes_no=True)}
    assert {row["subject"] for row in result.inputs if row["in_group"]} == lefties
    assert result.excluded["missing"] == []


@pytest.mark.django_db
def test_a_subject_with_two_records_in_the_run_is_refused(
    store: Callable[..., Record], mars_rising: Feature, profession: Variable, eph: Ephemeris
) -> None:
    twice = Subject.objects.create(label="twice")
    dataset = Dataset.objects.create(name="doubled")
    dataset.records.add(store(birth(0), subject=twice), store(birth(3), subject=twice))
    run = run_dataset(dataset, ChartSettings(aspect_angles=MAJOR), eph)
    with pytest.raises(AnalysisRefused, match=f"subject 'twice' has 2 records in run {run.pk}"):
        run_hypothesis(saved(mars_rising, profession, run))
    assert not AnalysisRun.objects.exists()


@pytest.mark.django_db
def test_every_run_of_a_hypothesis_is_kept(
    mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    tested = saved(mars_rising, profession, study)
    first, second = run_hypothesis(tested), run_hypothesis(tested)
    assert list(tested.results.order_by("pk")) == [first, second]
    assert first.result == second.result


@pytest.mark.django_db
def test_editing_a_value_after_a_result_leaves_the_result_as_it_was(
    mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    tested = saved(mars_rising, profession, study)
    before = run_hypothesis(tested)
    observation = Observation.objects.get(subject__label="study-0")
    observation.category = profession.categories.get(name="musician")
    observation.save()
    before.refresh_from_db()
    assert next(row for row in before.inputs if row["subject"] == "study-0")["in_group"] is True
    after = run_hypothesis(tested)
    assert next(row for row in after.inputs if row["subject"] == "study-0")["in_group"] is False


@pytest.mark.django_db
def test_a_hypothesis_is_checked_again_when_it_runs(
    mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    tested = saved(mars_rising, profession, study)
    mars_rising.kind, mars_rising.settings = "sign", {"point": "mars"}  # no result yet: allowed
    mars_rising.full_clean()
    mars_rising.save()
    tested.refresh_from_db()
    with pytest.raises(ValidationError, match="needs a yes/no feature"):
        run_hypothesis(tested)
    with pytest.raises(CommandError, match="needs a yes/no feature"):
        call_command("run_hypothesis", tested.name)
    assert not AnalysisRun.objects.exists()


# --- Locks ----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_hypothesis_with_a_result_can_t_change(
    mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    tested = saved(mars_rising, profession, study)
    tested.settings = {"alternative": "two-sided", "alpha": 0.05}
    tested.full_clean()  # no result yet: it can still change
    tested.save()
    run_hypothesis(tested)
    tested.full_clean()  # unchanged is fine
    for field, value in (
        ("settings", {"alternative": "greater", "alpha": 0.05}),
        ("statement", "Something else"),
        ("category", profession.categories.get(name="musician")),
    ):
        changed = Hypothesis.objects.get(pk=tested.pk)
        setattr(changed, field, value)
        with pytest.raises(
            ValidationError, match="Mars rising in athletes has results, so it can't change"
        ):
            changed.full_clean()


@pytest.mark.django_db
def test_a_feature_used_by_a_hypothesis_with_results_can_t_change(
    mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    tested = saved(mars_rising, profession, study)
    mars_rising.settings = {**mars_rising.settings, "orb": 45}
    mars_rising.full_clean()  # no result yet
    mars_rising.save()
    run_hypothesis(tested)
    mars_rising.full_clean()  # unchanged is fine
    mars_rising.settings = {**mars_rising.settings, "orb": 30}
    with pytest.raises(
        ValidationError,
        match="Mars rising is used by a hypothesis with results, so it can't change",
    ):
        mars_rising.full_clean()


@pytest.mark.django_db
def test_what_a_hypothesis_uses_can_t_be_deleted(
    mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    tested = saved(mars_rising, profession, study)
    with pytest.raises(ProtectedError):
        Feature.objects.get(pk=mars_rising.pk).delete()
    run_hypothesis(tested)
    with pytest.raises(ProtectedError):
        Hypothesis.objects.get(pk=tested.pk).delete()


@pytest.mark.django_db
def test_a_hypothesis_without_results_can_be_deleted(
    mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    saved(mars_rising, profession, study).delete()
    assert not Hypothesis.objects.exists()


# --- The admin ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_hypotheses_are_entered_in_the_admin_and_checked(
    admin_client: Any, mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    assert admin_client.get("/admin/research_platform/hypothesis/add/").status_code == 200
    form = {
        "name": "No group",
        "statement": "A statement",
        "feature": mars_rising.pk,
        "variable": profession.pk,
        "category": "",
        "run": study.pk,
        "analysis": "fisher-exact",
        "settings": '{"alternative": "greater", "alpha": 0.05}',
    }
    response = admin_client.post("/admin/research_platform/hypothesis/add/", form)
    assert response.status_code == 200  # the form again, with the reason
    assert "name which of profession&#x27;s categories is the group" in response.content.decode()
    assert not Hypothesis.objects.exists()


@pytest.mark.django_db
def test_results_can_t_be_added_or_changed_in_the_admin(
    admin_client: Any, mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    result = run_hypothesis(saved(mars_rising, profession, study))
    assert admin_client.get("/admin/research_platform/analysisrun/add/").status_code == 403
    page = admin_client.get(f"/admin/research_platform/analysisrun/{result.pk}/change/")
    assert page.status_code == 200 and 'name="_save"' not in page.content.decode()


# --- The command ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_command_runs_a_hypothesis_by_name(
    mars_rising: Feature, profession: Variable, study: CalculationRun
) -> None:
    saved(mars_rising, profession, study)
    out = io.StringIO()
    call_command("run_hypothesis", "Mars rising in athletes", stdout=out)
    result = AnalysisRun.objects.get()
    p = Fraction(result.result["p_value"])
    level = "at or below" if result.result["below_alpha"] else "above"
    assert out.getvalue().strip() == (
        f"result {result.pk} of 'Mars rising in athletes': p = {float(p):.4g} ({p}), {level} 0.05"
    )


@pytest.mark.django_db
def test_the_command_says_why_it_can_t_run(
    store: Callable[..., Record], mars_rising: Feature, profession: Variable, eph: Ephemeris
) -> None:
    with pytest.raises(CommandError, match="no hypothesis named 'nothing'"):
        call_command("run_hypothesis", "nothing")
    twice = Subject.objects.create(label="twice")
    dataset = Dataset.objects.create(name="doubled")
    dataset.records.add(store(birth(0), subject=twice), store(birth(3), subject=twice))
    run = run_dataset(dataset, ChartSettings(aspect_angles=MAJOR), eph)
    saved(mars_rising, profession, run, name="doubled")
    with pytest.raises(CommandError, match="subject 'twice' has 2 records"):
        call_command("run_hypothesis", "doubled")
