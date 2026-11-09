"""Running the engine over a dataset.

`run_dataset` computes a chart for each of a dataset's records under one set of settings, in one
process. Each chart is stored once, keyed by its calculation key, as its chart-result-v1 text;
a later run that needs the same chart reuses it. Each record gets one outcome per run: its chart,
or the engine's refusal of it, after which the run carries on. An environment failure stops the
run: nothing from it is kept but the run itself, marked failed with the error.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction

from astro_research.chart import ENGINE_VERSION, BirthRecord, ChartSettings, compute_chart
from astro_research.civil_time import UtcTime, ZonedLocalTime
from astro_research.ephemeris import Ephemeris, EphemerisNotLoaded
from astro_research.geo import GeoPosition
from astro_research.measurements import MAJOR, OCTILE
from astro_research.result_format import chart_to_json
from research_platform import runner
from research_platform.models import (
    CalculationRun,
    Dataset,
    Record,
    RunOutcome,
    StoredChart,
    Subject,
)
from research_platform.runner import run_dataset

LONDON = GeoPosition(latitude=51.5074, longitude=-0.1278)
SETTINGS = ChartSettings(aspect_angles=MAJOR)
GOOD = (
    BirthRecord(
        time=ZonedLocalTime(local=datetime(1987, 6, 15, 4, 25), zone="Europe/London"),
        place=LONDON,
    ),
    BirthRecord(
        time=UtcTime(utc=datetime(2024, 3, 20, 12, tzinfo=UTC)),
        place=GeoPosition(latitude=-33.8688, longitude=151.2093),
    ),
)
# Records the engine refuses, by the refusal's class.
REFUSED = {
    "AmbiguousLocalTime": BirthRecord(
        time=ZonedLocalTime(local=datetime(1987, 10, 25, 1, 30), zone="Europe/London"),
        place=LONDON,
    ),
    "DateOutsideEphemeris": BirthRecord(
        time=UtcTime(utc=datetime(1700, 1, 1, 12, tzinfo=UTC)), place=LONDON
    ),
    "DateOutsideUt1Data": BirthRecord(
        time=UtcTime(utc=datetime(2027, 1, 1, tzinfo=UTC)), place=LONDON
    ),
    "AnglesUndefined": BirthRecord(
        time=UtcTime(utc=datetime(2000, 1, 1, tzinfo=UTC)),
        place=GeoPosition(latitude=90.0, longitude=0.0),
    ),
}


def dataset_of(*records: Record, name: str = "sample") -> Dataset:
    dataset = Dataset.objects.create(name=name)
    dataset.records.add(*records)
    return dataset


@pytest.fixture
def two_records(store: Callable[..., Record]) -> tuple[Record, Record]:
    other = Subject.objects.create(label="subject-2")
    return store(GOOD[0]), store(GOOD[1], subject=other)


# --- Charts ---------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_run_stores_the_engine_s_chart_for_each_record(
    eph: Ephemeris, two_records: tuple[Record, Record]
) -> None:
    run = run_dataset(dataset_of(*two_records), SETTINGS, eph)
    assert run.status == CalculationRun.Status.FINISHED
    outcomes = {outcome.record: outcome for outcome in run.outcomes.all()}
    assert set(outcomes) == set(two_records)
    for record in two_records:
        chart = compute_chart(eph, record.birth_record(), SETTINGS)
        outcome = outcomes[record]
        assert outcome.chart is not None and outcome.refusal_class == ""
        assert outcome.chart.calculation_key == chart.calculation_key
        assert outcome.chart.chart() == chart


@pytest.mark.django_db
def test_a_stored_chart_is_the_text_the_format_wrote(
    eph: Ephemeris, two_records: tuple[Record, Record]
) -> None:
    # Stored as text, not jsonb: jsonb reorders keys, writes 0.0 for -0.0 and rewrites
    # exponents (observed on PostgreSQL 18.6).
    run_dataset(dataset_of(*two_records), SETTINGS, eph)
    for record in two_records:
        chart = compute_chart(eph, record.birth_record(), SETTINGS)
        assert StoredChart.objects.get(pk=chart.calculation_key).document == chart_to_json(chart)


@pytest.mark.django_db
def test_a_run_records_what_it_ran_with(eph: Ephemeris, two_records: tuple[Record, Record]) -> None:
    dataset = dataset_of(*two_records)
    settings = ChartSettings(aspect_angles=(45.0, 135.0), points=SETTINGS.points[:3])
    run = run_dataset(dataset, settings, eph)
    run.refresh_from_db()
    assert run.dataset == dataset
    assert run.chart_settings() == settings
    assert run.engine_version == ENGINE_VERSION
    assert run.ephemeris_provenance() == eph.provenance
    assert run.finished_at is not None and run.started_at <= run.finished_at


# --- Reuse ----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_later_run_reuses_the_charts_already_stored(
    eph: Ephemeris, two_records: tuple[Record, Record]
) -> None:
    dataset = dataset_of(*two_records)
    first = run_dataset(dataset, SETTINGS, eph)
    documents = dict(StoredChart.objects.values_list("calculation_key", "document"))
    second = run_dataset(dataset, SETTINGS, eph)
    assert dict(StoredChart.objects.values_list("calculation_key", "document")) == documents
    keys = [
        sorted(run.outcomes.values_list("chart__calculation_key", flat=True))
        for run in (first, second)
    ]
    assert keys[0] == keys[1] == sorted(documents)
    assert second.computed == 0 and second.reused == 2 and first.computed == 2


@pytest.mark.django_db
def test_other_settings_give_other_charts(
    eph: Ephemeris, two_records: tuple[Record, Record]
) -> None:
    dataset = dataset_of(*two_records)
    run_dataset(dataset, SETTINGS, eph)
    run_dataset(dataset, ChartSettings(aspect_angles=tuple(sorted({*MAJOR, *OCTILE}))), eph)
    assert StoredChart.objects.count() == 4


# --- Refusals -------------------------------------------------------------------------------


@pytest.mark.django_db
def test_refused_records_are_recorded_and_the_run_carries_on(
    eph: Ephemeris, store: Callable[..., Record]
) -> None:
    good = store(GOOD[0])
    refused = {name: store(birth_record) for name, birth_record in REFUSED.items()}
    run = run_dataset(dataset_of(good, *refused.values()), SETTINGS, eph)
    assert run.status == CalculationRun.Status.FINISHED
    outcomes = {outcome.record: outcome for outcome in run.outcomes.all()}
    assert outcomes[good].chart is not None
    for name, record in refused.items():
        outcome = outcomes[record]
        assert (outcome.chart, outcome.refusal_class) == (None, name)
        assert outcome.refusal_message
    assert StoredChart.objects.count() == 1
    assert (run.computed, run.reused, run.refused) == (1, 0, 4)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "error", [EphemerisNotLoaded("the files moved"), RuntimeError("a bug")], ids=type
)
def test_a_failure_that_isn_t_a_refusal_stops_the_run_and_keeps_none_of_it(
    eph: Ephemeris,
    two_records: tuple[Record, Record],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    # The second record fails: the first's outcome and chart must not be kept either.
    calls = []

    def failing_on_the_second(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        if len(calls) == 2:
            raise error
        return compute_chart(*args, **kwargs)

    monkeypatch.setattr(runner, "compute_chart", failing_on_the_second)
    dataset = dataset_of(*two_records)
    with pytest.raises(type(error)):
        run_dataset(dataset, SETTINGS, eph)
    run = CalculationRun.objects.get(dataset=dataset)
    assert run.status == CalculationRun.Status.FAILED
    assert (run.error_class, run.error_message) == (type(error).__name__, str(error))
    assert not run.outcomes.exists()
    assert not StoredChart.objects.exists()


# --- Constraints ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_record_has_one_outcome_per_run(
    eph: Ephemeris, two_records: tuple[Record, Record]
) -> None:
    run = run_dataset(dataset_of(*two_records), SETTINGS, eph)
    existing = run.outcomes.first()
    assert existing is not None
    with transaction.atomic(), pytest.raises(IntegrityError):
        RunOutcome.objects.create(run=run, record=existing.record, chart=existing.chart)


@pytest.mark.django_db
def test_an_outcome_is_a_chart_or_a_refusal(
    eph: Ephemeris, two_records: tuple[Record, Record]
) -> None:
    run = run_dataset(dataset_of(two_records[0]), SETTINGS, eph)
    stored = StoredChart.objects.get()
    with transaction.atomic(), pytest.raises(IntegrityError):  # neither
        RunOutcome.objects.create(run=run, record=two_records[1])
    with transaction.atomic(), pytest.raises(IntegrityError):  # both
        RunOutcome.objects.create(
            run=run, record=two_records[1], chart=stored, refusal_class="AnglesUndefined"
        )


# --- The command ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_command_runs_a_dataset(two_records: tuple[Record, Record]) -> None:
    dataset_of(*two_records, name="sample")
    out = StringIO()
    call_command("compute_dataset", "sample", "--aspects", "major", stdout=out)
    run = CalculationRun.objects.get()
    assert run.status == CalculationRun.Status.FINISHED
    assert run.chart_settings() == SETTINGS
    assert f"run {run.pk}: 2 charts (2 computed, 0 reused), 0 refused" in out.getvalue()
