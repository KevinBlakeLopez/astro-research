"""python manage.py compute_dataset NAME --aspects major,octile [--time-scale ut1-from-iers]"""

import argparse
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from research_platform.chart_settings import (
    DEFAULT_TIME_SCALE,
    TIME_SCALES,
    InvalidSettings,
    chart_settings,
)
from research_platform.models import CalculationRun, Dataset
from research_platform.runner import run_dataset


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
            default=DEFAULT_TIME_SCALE,
            help=f"how UTC becomes UT1 (default {DEFAULT_TIME_SCALE})",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            dataset = Dataset.objects.get(name=options["dataset"])
        except Dataset.DoesNotExist:
            raise CommandError(f"no dataset named {options['dataset']!r}") from None
        try:
            settings = chart_settings(options["aspects"], options["time_scale"])
        except InvalidSettings as error:
            raise CommandError(str(error)) from None
        run: CalculationRun = run_dataset(dataset, settings)
        charts = run.computed + run.reused
        self.stdout.write(
            f"run {run.pk}: {charts} charts ({run.computed} computed, {run.reused} reused), "
            f"{run.refused} refused"
        )
