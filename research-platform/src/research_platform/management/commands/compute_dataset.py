"""python manage.py compute_dataset NAME --aspects major,octile [--time-scale ut1-from-iers]"""

import argparse
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from astro_research.astronomical_time import TimeScale
from astro_research.chart import ChartSettings
from astro_research.measurements import MAJOR, OCTILE
from research_platform.models import CalculationRun, Dataset
from research_platform.runner import run_dataset

ASPECT_SETS = {"major": MAJOR, "octile": OCTILE}
TIME_SCALES = {
    "clock-as-ut1": TimeScale.CLOCK_AS_UT1,
    "ut1-from-delta-t": TimeScale.UT1_FROM_DELTA_T,
    "ut1-from-iers": TimeScale.UT1_FROM_IERS,
}


class Command(BaseCommand):
    help = "Compute a chart for each record of a dataset, under one set of settings."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("dataset", help="the dataset's name")
        parser.add_argument(
            "--aspects",
            required=True,
            help="comma-separated aspect angles in degrees and/or the sets major and octile",
        )
        parser.add_argument(
            "--time-scale",
            choices=sorted(TIME_SCALES),
            default="ut1-from-iers",
            help="how UTC becomes UT1 (default ut1-from-iers)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            dataset = Dataset.objects.get(name=options["dataset"])
        except Dataset.DoesNotExist:
            raise CommandError(f"no dataset named {options['dataset']!r}") from None
        settings = ChartSettings(
            aspect_angles=_aspect_angles(options["aspects"]),
            time_scale=TIME_SCALES[options["time_scale"]],
        )
        run: CalculationRun = run_dataset(dataset, settings)
        charts = run.computed + run.reused
        self.stdout.write(
            f"run {run.pk}: {charts} charts ({run.computed} computed, {run.reused} reused), "
            f"{run.refused} refused"
        )


def _aspect_angles(text: str) -> tuple[float, ...]:
    angles: set[float] = set()
    for item in text.split(","):
        name = item.strip().lower()
        if name in ASPECT_SETS:
            angles.update(ASPECT_SETS[name])
            continue
        try:
            angles.add(float(name))
        except ValueError:
            raise CommandError(f"--aspects: {item!r} is neither an angle nor a set name") from None
    return tuple(sorted(angles))
