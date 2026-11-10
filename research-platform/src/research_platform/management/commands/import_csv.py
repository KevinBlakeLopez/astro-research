"""python manage.py import_csv FILE --source ID [--dataset NAME] [--add-to-existing] [--check]

Imports records and variable values from a CSV file with a row per subject, all or none (see
`research_platform.csv_import` for the columns). The source cites every record and value. If
any row is refused, nothing is imported, and every refused row is listed by its line.
"""

import argparse
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from research_platform.csv_import import FileRefused, counted, plan_import, read_text, save
from research_platform.models import Dataset, Source


class Command(BaseCommand):
    help = "Import records and variable values from CSV, a row per subject: all of them, or none."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("file", type=Path, help="a CSV file, a row per subject")
        parser.add_argument(
            "--source", type=int, required=True, help="the id of the source every row cites"
        )
        parser.add_argument("--dataset", help="the name of a dataset to add the records to")
        parser.add_argument(
            "--add-to-existing",
            action="store_true",
            help="a subject already known, or on two lines, is the same person",
        )
        parser.add_argument("--check", action="store_true", help="check, but import nothing")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            source = Source.objects.get(pk=options["source"])
        except Source.DoesNotExist:
            raise CommandError(f"no source with id {options['source']}") from None
        dataset = None
        if options["dataset"] is not None:
            try:
                dataset = Dataset.objects.get(name=options["dataset"])
            except Dataset.DoesNotExist:
                raise CommandError(f"no dataset named {options['dataset']!r}") from None
        try:
            text = read_text(options["file"].read_bytes())
            plan = plan_import(text, source, add_to_existing=options["add_to_existing"])
        except FileRefused as error:
            raise CommandError(f"nothing was imported: {error}") from None
        if plan.refused:
            lines = [f"  line {line}: {reason}" for line, reason in plan.refused.items()]
            raise CommandError(
                f"nothing was imported: {len(plan.refused)} of {plan.total} rows refused\n"
                + "\n".join(lines)
            )
        records, values = counted(len(plan.rows), "record"), counted(plan.values, "value")
        if options["check"]:
            self.stdout.write(f"{records} can be imported, with {values}; nothing was saved")
            return
        save(plan, source, dataset)
        subjects = counted(plan.new_subjects, "new subject")
        self.stdout.write(f"imported {records}: {subjects}, {values}")
