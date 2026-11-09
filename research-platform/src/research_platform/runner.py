"""Running the engine over a dataset, in one process.

At the brief's scale (100-200 curated charts) a run takes well under a second: computing a chart
takes about 0.5 ms and writing its document about 2 ms (measured). The engine is stateless per
call, so processes can be added here later without changing it.

A run:
1. is recorded first, as running, with the settings, engine version and ephemeris provenance;
2. then, in one transaction, gives each record an outcome. The chart is reused when one is
   stored under the record's calculation key (computable without calculating), and otherwise
   computed and stored. The engine's refusal of a record is recorded and the run carries on;
3. ends finished, with its counts. Any other error rolls back every outcome and chart of the
   run, marks it failed with the error, and is raised again.
"""

from django.conf import settings as django_settings
from django.db import transaction
from django.utils import timezone

from astro_research.angles import AnglesUndefined
from astro_research.astronomical_time import InstantInLeapSecond
from astro_research.chart import (
    ENGINE_VERSION,
    ChartSettings,
    InvalidBirthRecord,
    calculation_key,
    compute_chart,
)
from astro_research.civil_time import CivilTimeError
from astro_research.ephemeris import DateOutsideEphemeris, Ephemeris
from astro_research.geo import InvalidCoordinates
from astro_research.result_format import chart_to_json
from research_platform.models import CalculationRun, Dataset, RunOutcome, StoredChart

# The engine's refusals of one record. They're recorded and the run carries on; anything else
# stops it. DateOutsideEphemeris (with DateOutsideUt1Data and DateOutsideLeapSeconds) is an
# EphemerisEnvironmentError in the engine, but a date outside the data is the record's, so it is
# listed here, before the environment errors that must stop a run.
REFUSALS: tuple[type[Exception], ...] = (
    CivilTimeError,
    InvalidCoordinates,
    InvalidBirthRecord,
    InstantInLeapSecond,
    AnglesUndefined,
    DateOutsideEphemeris,
)


def run_dataset(
    dataset: Dataset, settings: ChartSettings, eph: Ephemeris | None = None
) -> CalculationRun:
    """Every record of `dataset` under `settings`; opens the configured ephemeris if not given."""
    if eph is None:
        eph = Ephemeris.open(django_settings.EPHE_DIR)
    run = CalculationRun.objects.create(
        dataset=dataset,
        settings=CalculationRun.encode_settings(settings),
        engine_version=ENGINE_VERSION,
        ephemeris=CalculationRun.encode_ephemeris(eph.provenance),
        started_at=timezone.now(),
    )
    try:
        with transaction.atomic():
            _give_outcomes(run, dataset, settings, eph)
    except Exception as error:
        run.status = CalculationRun.Status.FAILED
        run.error_class = type(error).__name__
        run.error_message = str(error)
        run.computed = run.reused = run.refused = 0
        run.finished_at = timezone.now()
        run.save()
        raise
    run.status = CalculationRun.Status.FINISHED
    run.finished_at = timezone.now()
    run.save()
    return run


def _give_outcomes(
    run: CalculationRun, dataset: Dataset, settings: ChartSettings, eph: Ephemeris
) -> None:
    for record in dataset.records.order_by("pk"):
        try:
            birth_record = record.birth_record()
            key = calculation_key(birth_record, settings, eph.provenance)
            stored = StoredChart.objects.filter(pk=key).first()
            if stored is None:
                chart = compute_chart(eph, birth_record, settings)
                stored = StoredChart.objects.create(
                    calculation_key=chart.calculation_key, document=chart_to_json(chart)
                )
                run.computed += 1
            else:
                run.reused += 1
        except REFUSALS as refusal:
            RunOutcome.objects.create(
                run=run,
                record=record,
                refusal_class=type(refusal).__name__,
                refusal_message=str(refusal),
            )
            run.refused += 1
            continue
        RunOutcome.objects.create(run=run, record=record, chart=stored)
