"""python manage.py run_hypothesis NAME"""

import argparse
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from research_platform import analyses
from research_platform.hypotheses import AnalysisRefused, run_hypothesis
from research_platform.models import Hypothesis


class Command(BaseCommand):
    help = "Run a hypothesis's analysis on its calculation run, and record the result."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("hypothesis", help="the hypothesis's name")

    def handle(self, *args: Any, **options: Any) -> None:
        name = options["hypothesis"]
        try:
            hypothesis = Hypothesis.objects.get(name=name)
        except Hypothesis.DoesNotExist:
            raise CommandError(f"no hypothesis named {name!r}") from None
        try:
            result = run_hypothesis(hypothesis)
        except AnalysisRefused as error:
            raise CommandError(str(error)) from None
        except ValidationError as error:
            raise CommandError("; ".join(error.messages)) from None
        summary = analyses.analysis(hypothesis.analysis).summarize(
            result.result, hypothesis.settings
        )
        self.stdout.write(f"result {result.pk} of {hypothesis.name!r}: {summary}")
