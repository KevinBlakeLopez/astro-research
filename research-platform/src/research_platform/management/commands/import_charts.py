"""python manage.py import_charts FILE... --source ID [--subject LABEL] [--add-to-existing]
[--resolution minute] [--dataset NAME] [--check]

Imports AstroChart Bridge's `.astrochart.json` files (version 1) as records, all or none. Each
chart becomes an offset-time record: the wall-clock reading and the offset its source stated,
the engine's `OffsetLocalTime`. The format has no time resolution, which is left blank unless
`--resolution` states it for the batch, and no elevation, which is the platform's default.

- **The source** (`--source`) is yours to cite: AstroChart Bridge's `site` says only where it
  read the chart.
- **The subject** is the file's name (`labels.name`), or `--subject` for every file of the
  batch. A file with neither is refused. So is a name that's already a subject, or that another
  file of the batch also gives, unless `--add-to-existing` says they're the same person.
- **Each file is kept** exactly as imported (`ImportedChart`), once.
- **All or nothing:** if any file is refused, by the format, by the engine or as a duplicate,
  nothing is imported, and every refused file is listed with its reason.
"""

import argparse
import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from astro_research.chart import BirthRecord, TimeResolution
from astro_research.civil_time import OffsetLocalTime
from astro_research.geo import GeoPosition
from research_platform.astrochart import PortableChart, RefusedChart, read
from research_platform.models import Dataset, ImportedChart, Record, Source, Subject


@dataclass(frozen=True)
class Planned:
    """A file that can be imported: its text, its chart, and the record it becomes."""

    path: Path
    text: str
    sha256: str
    chart: PortableChart
    label: str
    birth_record: BirthRecord


class Command(BaseCommand):
    help = "Import AstroChart Bridge charts (.astrochart.json) as records: all of them, or none."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("files", nargs="+", type=Path, help=".astrochart.json files")
        parser.add_argument(
            "--source", type=int, required=True, help="the id of the source every chart cites"
        )
        parser.add_argument(
            "--subject", help="the subject of every chart in the batch, instead of their names"
        )
        parser.add_argument(
            "--add-to-existing",
            action="store_true",
            help="let charts join a subject that exists, or that another file also names",
        )
        parser.add_argument(
            "--resolution",
            choices=[resolution.value for resolution in TimeResolution],
            help="the time resolution the source records, for every chart in the batch",
        )
        parser.add_argument("--dataset", help="the name of a dataset to add the records to")
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
        resolution = TimeResolution(options["resolution"]) if options["resolution"] else None

        paths: list[Path] = options["files"]
        planned, refused = _plan(paths, options["subject"], resolution)
        refused |= _subject_refusals(planned, options["subject"], options["add_to_existing"])
        if refused:
            lines = [f"  {path}: {reason}" for path, reason in refused.items()]
            raise CommandError(
                f"nothing was imported: {len(refused)} of {len(paths)} files refused\n"
                + "\n".join(lines)
            )
        if options["check"]:
            self.stdout.write(f"{_count(len(planned), 'chart')} can be imported; nothing was saved")
            return
        with transaction.atomic():
            new = _save(planned, source, dataset)
        self.stdout.write(f"imported {_count(len(planned), 'chart')}: {_count(new, 'new subject')}")


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _plan(
    paths: list[Path], subject: str | None, resolution: TimeResolution | None
) -> tuple[list[Planned], dict[str, str]]:
    """Each file read and checked: the ones that can be imported, and the others' reasons."""
    planned: list[Planned] = []
    refused: dict[str, str] = {}
    seen: set[str] = set()
    for path in paths:
        try:
            content = path.read_bytes()
        except OSError as error:
            refused[str(path)] = f"unreadable: {error.strerror}"
            continue
        sha256 = hashlib.sha256(content).hexdigest()
        if sha256 in seen or ImportedChart.objects.filter(sha256=sha256).exists():
            refused[str(path)] = "duplicate"
            continue
        seen.add(sha256)
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            refused[str(path)] = "not-utf-8"
            continue
        try:
            chart = read(text)
        except RefusedChart as refusal:
            refused[str(path)] = refusal.reason
            continue
        label = subject if subject is not None else chart.labels.get("name")
        if label is None:
            refused[str(path)] = "no-name"
            continue
        try:  # the engine's refusals of a record are ValueErrors
            birth_record = BirthRecord(
                time=OffsetLocalTime(
                    local=datetime.fromisoformat(chart.local),
                    utc_offset=timedelta(seconds=chart.utc_offset_seconds),
                ),
                place=GeoPosition(latitude=chart.latitude, longitude=chart.longitude),
                time_resolution=resolution,
            )
        except ValueError as error:
            refused[str(path)] = f"invalid-record: {error}"
            continue
        planned.append(Planned(path, text, sha256, chart, label, birth_record))
    return planned, refused


def _subject_refusals(
    planned: list[Planned], subject: str | None, add_to_existing: bool
) -> dict[str, str]:
    """A name that's a subject already, or that several files give, unless they're said to be
    the same person. `--subject` says so for its files."""
    if add_to_existing:
        return {}
    existing = set(
        Subject.objects.filter(label__in={p.label for p in planned}).values_list("label", flat=True)
    )
    shared = Counter(p.label for p in planned)
    refused = {}
    for each in planned:
        if each.label in existing:
            refused[str(each.path)] = "existing-subject"
        elif subject is None and shared[each.label] > 1:
            refused[str(each.path)] = "shared-name"
    return refused


def _save(planned: list[Planned], source: Source, dataset: Dataset | None) -> int:
    """Saves every planned file's record and the file; the number of subjects created."""
    subjects: dict[str, Subject] = {}
    new = 0
    records = []
    for each in planned:
        if each.label not in subjects:
            subjects[each.label], created = Subject.objects.get_or_create(label=each.label)
            new += created
        record = Record.from_birth_record(
            each.birth_record, subject=subjects[each.label], source=source
        )
        record.full_clean()
        record.save()
        ImportedChart.objects.create(
            record=record,
            file_name=each.path.name,
            text=each.text,
            sha256=each.sha256,
            site=each.chart.source.get("site", ""),
            via=each.chart.source.get("via", ""),
            zone_label=each.chart.source.get("zoneLabel", ""),
            utc_basis=each.chart.source.get("utcBasis", ""),
        )
        records.append(record)
    if dataset is not None:
        dataset.records.add(*records)
    return new
