"""Subjects, sources, birth records, datasets, calculation runs and stored charts.

These are kept apart, as the brief asks:

- a `Subject` is someone or something with a chart, identified by a label you choose;
- a `Source` is where a record came from;
- a `Record` is one source's birth data for one subject, in the engine's `BirthRecord` fields;
  a subject can have several, and they may disagree;
- a `Dataset` chooses records, so which record stands for a subject is explicit;
- a `CalculationRun` computes a dataset's charts under one set of settings, and has one
  `RunOutcome` per record: its chart, or the engine's refusal of it;
- a `StoredChart` is one chart's chart-result-v1 text, kept once under its calculation key and
  shared by every run that produced it;
- an `ImportedChart` is the AstroChart Bridge file a record was imported from, exactly as it was.

What's known about each subject is in variables:

- a `Variable` is a category, a number or yes/no; a category variable lists its `Category`s;
- an `Observation` is one subject's value of one variable, from a cited source. A subject has
  at most one per variable; one without is missing.

Whether a variable is an outcome or something to control for is a hypothesis's to say.

A `Feature` (a derived configuration) is something you define from a chart: a kind from the
registry in `features.py` and the kind's settings, such as "Mars within 10 degrees of an angle".
Its value comes from a stored chart when needed and is never stored.

A `Hypothesis` names a feature, an outcome (a variable and the group: one of its categories,
or yes), a finished calculation run, and an analysis from the registry in `analyses.py` with
its settings. Each `AnalysisRun` is one result, with the inputs it used, and every one is kept.
A hypothesis is locked once it has a result, and so is its feature.
"""

import dataclasses
import json
import math
from datetime import datetime, timedelta
from typing import Any, assert_never

from django.core.exceptions import ValidationError
from django.db import models

from astro_research.astronomical_time import TimeScale
from astro_research.chart import BirthRecord, Chart, ChartSettings, TimeResolution
from astro_research.civil_time import (
    CivilTime,
    LocalMeanTime,
    OffsetLocalTime,
    UtcTime,
    ZonedLocalTime,
)
from astro_research.ephemeris import EphemerisProvenance
from astro_research.geo import GeoPosition
from astro_research.positions import Body, LunarPoint, Point
from astro_research.result_format import chart_from_json
from research_platform import analyses, features

_POINTS: dict[str, Point] = {member.value: member for member in (*Body, *LunarPoint)}


class Subject(models.Model):
    label = models.CharField(max_length=200, unique=True)
    notes = models.TextField(blank=True)

    def __str__(self) -> str:
        return self.label


class Source(models.Model):
    citation = models.TextField()
    reliability = models.CharField(
        max_length=50, blank=True, help_text="The source's own rating, as it gives it."
    )
    retrieved_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    def __str__(self) -> str:
        return self.citation


class InvalidRecord(ValueError):
    """The stored fields don't describe one kind of civil time."""


class Record(models.Model):
    """One source's birth data for one subject: the engine's `BirthRecord`, field by field."""

    class TimeKind(models.TextChoices):
        UTC = "utc"
        ZONED_LOCAL_TIME = "zoned_local_time"
        OFFSET_LOCAL_TIME = "offset_local_time"
        LOCAL_MEAN_TIME = "local_mean_time"

    class Resolution(models.TextChoices):
        HOUR = "hour"
        MINUTE = "minute"
        SECOND = "second"

    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="records")
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="records")
    time_kind = models.CharField(max_length=20, choices=TimeKind.choices)
    # ISO 8601. Aware (+00:00) for UTC; otherwise the naive wall-clock reading, which a
    # DateTimeField can't hold while Django keeps times zone-aware.
    reading = models.CharField(max_length=40)
    zone = models.CharField(max_length=64, blank=True, help_text="IANA zone; zoned times only.")
    # The stated offset of an offset time, or the offset stated to settle a zoned time that
    # happened twice; whole microseconds.
    utc_offset_us = models.BigIntegerField(null=True, blank=True)
    accept_pre_1970_tzdb = models.BooleanField(default=False)
    latitude = models.FloatField()
    longitude = models.FloatField()
    elevation_m = models.FloatField(default=0.0)
    time_resolution = models.CharField(max_length=10, choices=Resolution.choices, blank=True)
    stated_uncertainty_us = models.BigIntegerField(null=True, blank=True)

    @classmethod
    def from_birth_record(
        cls, birth_record: BirthRecord, *, subject: Subject, source: Source
    ) -> "Record":
        """An unsaved record holding `birth_record`'s fields."""
        time = birth_record.time
        fields: dict[str, Any] = {}
        match time:
            case UtcTime():
                fields = {"time_kind": cls.TimeKind.UTC, "reading": time.utc.isoformat()}
            case ZonedLocalTime():
                fields = {
                    "time_kind": cls.TimeKind.ZONED_LOCAL_TIME,
                    "reading": time.local.isoformat(),
                    "zone": time.zone,
                    "utc_offset_us": _microseconds(time.stated_utc_offset),
                    "accept_pre_1970_tzdb": time.accept_pre_1970_tzdb,
                }
            case OffsetLocalTime():
                fields = {
                    "time_kind": cls.TimeKind.OFFSET_LOCAL_TIME,
                    "reading": time.local.isoformat(),
                    "utc_offset_us": _microseconds(time.utc_offset),
                }
            case LocalMeanTime():
                fields = {
                    "time_kind": cls.TimeKind.LOCAL_MEAN_TIME,
                    "reading": time.local.isoformat(),
                }
            case _:
                assert_never(time)
        resolution = birth_record.time_resolution
        return cls(
            subject=subject,
            source=source,
            **fields,
            latitude=birth_record.place.latitude,
            longitude=birth_record.place.longitude,
            elevation_m=birth_record.place.elevation_m,
            time_resolution="" if resolution is None else resolution.value,
            stated_uncertainty_us=_microseconds(birth_record.stated_uncertainty),
        )

    def birth_record(self) -> BirthRecord:
        """The engine's record. Raises the engine's refusal, or InvalidRecord, if it isn't one."""
        return BirthRecord(
            time=self._civil_time(),
            place=GeoPosition(
                latitude=self.latitude, longitude=self.longitude, elevation_m=self.elevation_m
            ),
            time_resolution=TimeResolution(self.time_resolution) if self.time_resolution else None,
            stated_uncertainty=_duration(self.stated_uncertainty_us),
        )

    def clean(self) -> None:
        try:
            self.birth_record()
        except ValueError as error:  # the engine's refusals of a record are ValueErrors
            raise ValidationError(str(error)) from error

    def _civil_time(self) -> CivilTime:
        try:
            reading = datetime.fromisoformat(self.reading)
        except ValueError:
            raise InvalidRecord(f"reading {self.reading!r} is not ISO 8601") from None
        # Whether the reading is aware (UTC) or naive (wall clock), the engine checks.
        if self.zone and self.time_kind != self.TimeKind.ZONED_LOCAL_TIME:
            raise InvalidRecord("only a zoned time has a zone")
        if self.utc_offset_us is not None and self.time_kind in (
            self.TimeKind.UTC,
            self.TimeKind.LOCAL_MEAN_TIME,
        ):
            raise InvalidRecord(f"a {self.time_kind} time has no offset")
        if self.accept_pre_1970_tzdb and self.time_kind != self.TimeKind.ZONED_LOCAL_TIME:
            raise InvalidRecord("only a zoned time can accept tzdb before 1970")
        match self.time_kind:
            case self.TimeKind.UTC:
                return UtcTime(utc=reading)
            case self.TimeKind.ZONED_LOCAL_TIME:
                return ZonedLocalTime(
                    local=reading,
                    zone=self.zone,
                    stated_utc_offset=_duration(self.utc_offset_us),
                    accept_pre_1970_tzdb=self.accept_pre_1970_tzdb,
                )
            case self.TimeKind.OFFSET_LOCAL_TIME:
                offset = _duration(self.utc_offset_us)
                if offset is None:
                    raise InvalidRecord("an offset time needs its offset")
                return OffsetLocalTime(local=reading, utc_offset=offset)
            case self.TimeKind.LOCAL_MEAN_TIME:
                return LocalMeanTime(local=reading)
            case kind:
                raise InvalidRecord(f"unknown time kind {kind!r}")

    def __str__(self) -> str:
        return f"{self.subject}: {self.reading} ({self.source})"


class ImportedChart(models.Model):
    """The AstroChart Bridge file (`.astrochart.json`) a record was imported from, kept exactly
    as it was, once: its SHA-256 is unique. Where AstroChart Bridge read the chart and how it
    decided the offset are copied into columns, to filter by; the text is the record of it."""

    record = models.OneToOneField(Record, on_delete=models.CASCADE, related_name="imported_chart")
    file_name = models.CharField(max_length=255)
    text = models.TextField()
    sha256 = models.CharField(max_length=64, unique=True)
    imported_at = models.DateTimeField(auto_now_add=True)
    site = models.CharField(max_length=32, blank=True, help_text="e.g. astro-seek, astro-com")
    via = models.CharField(max_length=32, blank=True, help_text="page, screenshot or paste")
    zone_label = models.CharField(max_length=64, blank=True)
    utc_basis = models.CharField(
        max_length=32, blank=True, help_text="printed, declared, zone-label or entered"
    )

    def __str__(self) -> str:
        return self.file_name


class Dataset(models.Model):
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    records = models.ManyToManyField(Record, related_name="datasets", blank=True)

    def __str__(self) -> str:
        return self.name


class StoredChart(models.Model):
    """One chart's chart-result-v1 document, as the format wrote it: text, not jsonb, which
    would reorder keys, write 0.0 for -0.0 and rewrite exponents (observed)."""

    calculation_key = models.CharField(max_length=64, primary_key=True)
    document = models.TextField()
    stored_at = models.DateTimeField(auto_now_add=True)

    def chart(self) -> Chart:
        return chart_from_json(self.document)


class CalculationRun(models.Model):
    """A dataset's charts under one set of settings, with what the run computed them with."""

    class Status(models.TextChoices):
        RUNNING = "running"
        FINISHED = "finished"
        FAILED = "failed"

    dataset = models.ForeignKey(Dataset, on_delete=models.PROTECT, related_name="runs")
    settings = models.TextField(help_text="The chart settings, as JSON.")
    engine_version = models.CharField(max_length=50)
    ephemeris = models.TextField(help_text="The verified ephemeris provenance, as JSON.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    error_class = models.CharField(max_length=200, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    computed = models.PositiveIntegerField(default=0)
    reused = models.PositiveIntegerField(default=0)
    refused = models.PositiveIntegerField(default=0)

    @staticmethod
    def encode_settings(settings: ChartSettings) -> str:
        return json.dumps(
            {
                "time_scale": settings.time_scale.value,
                "aspect_angles": list(settings.aspect_angles),
                "points": [point.value for point in settings.points],
            }
        )

    def chart_settings(self) -> ChartSettings:
        raw = json.loads(self.settings)
        return ChartSettings(
            aspect_angles=tuple(raw["aspect_angles"]),
            time_scale=TimeScale(raw["time_scale"]),
            points=tuple(_POINTS[value] for value in raw["points"]),
        )

    @staticmethod
    def encode_ephemeris(provenance: EphemerisProvenance) -> str:
        return json.dumps(dataclasses.asdict(provenance))

    def ephemeris_provenance(self) -> EphemerisProvenance:
        raw = json.loads(self.ephemeris)
        files = tuple((name, sha256) for name, sha256 in raw.pop("file_sha256"))
        return EphemerisProvenance(**raw, file_sha256=files)

    def __str__(self) -> str:
        return f"run {self.pk} of {self.dataset} ({self.status})"


class RunOutcome(models.Model):
    """What one run made of one record: its chart, or the engine's refusal of it."""

    run = models.ForeignKey(CalculationRun, on_delete=models.CASCADE, related_name="outcomes")
    record = models.ForeignKey(Record, on_delete=models.PROTECT, related_name="outcomes")
    chart = models.ForeignKey(
        StoredChart, on_delete=models.PROTECT, null=True, blank=True, related_name="outcomes"
    )
    refusal_class = models.CharField(max_length=100, blank=True)
    refusal_message = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "record"], name="one_outcome_per_record"),
            models.CheckConstraint(
                condition=(models.Q(chart__isnull=False) & models.Q(refusal_class=""))
                | (models.Q(chart__isnull=True) & ~models.Q(refusal_class="")),
                name="a_chart_or_a_refusal",
            ),
        ]


class Variable(models.Model):
    """Something recorded about subjects: a category, a number or yes/no."""

    class Kind(models.TextChoices):
        CATEGORY = "category", "category"
        NUMBER = "number", "number"
        YES_NO = "yes-no", "yes/no"

    name = models.CharField(max_length=200, unique=True)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    unit = models.CharField(max_length=50, blank=True, help_text="For a number, e.g. cm.")
    description = models.TextField(blank=True)

    def clean(self) -> None:
        """A variable keeps its kind once it has values or categories, which would otherwise
        stop meaning anything."""
        if self.pk is None:
            return
        stored = Variable.objects.filter(pk=self.pk).values_list("kind", flat=True).first()
        if stored is None or stored == self.kind:
            return
        if self.observations.exists():
            raise ValidationError(f"{self.name} has values, so its kind can't change")
        if self.categories.exists():
            raise ValidationError(
                f"{self.name} has categories, so it stays a category variable until they're deleted"
            )

    def __str__(self) -> str:
        return self.name


class Category(models.Model):
    """One of a category variable's values."""

    variable = models.ForeignKey(Variable, on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(max_length=200)

    class Meta:
        ordering = ["pk"]  # in the order they were added
        verbose_name_plural = "categories"
        constraints = [
            models.UniqueConstraint(fields=["variable", "name"], name="one_category_per_name"),
        ]

    def clean(self) -> None:
        if self.variable_id is not None and self.variable.kind != Variable.Kind.CATEGORY:
            raise ValidationError("only a category variable has categories")

    def __str__(self) -> str:
        return f"{self.variable.name}: {self.name}"


_VALUE_FIELD = {
    Variable.Kind.CATEGORY: "category",
    Variable.Kind.NUMBER: "number",
    Variable.Kind.YES_NO: "yes_no",
}
_VALUE_WORDS = {
    Variable.Kind.CATEGORY: "one of its categories",
    Variable.Kind.NUMBER: "a number",
    Variable.Kind.YES_NO: "yes or no",
}


class Observation(models.Model):
    """One subject's value of one variable, from a source: exactly one of a category, a number
    and yes/no, the one its variable's kind names."""

    variable = models.ForeignKey(Variable, on_delete=models.PROTECT, related_name="observations")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="observations")
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="observations")
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, null=True, blank=True, related_name="observations"
    )
    number = models.FloatField(null=True, blank=True)
    yes_no = models.BooleanField(null=True, blank=True)
    notes = models.TextField(blank=True, help_text="Why this value, if sources disagree.")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["variable", "subject"], name="one_value_per_subject"),
            models.CheckConstraint(
                condition=(
                    models.Q(category__isnull=False)
                    & models.Q(number__isnull=True)
                    & models.Q(yes_no__isnull=True)
                )
                | (
                    models.Q(category__isnull=True)
                    & models.Q(number__isnull=False)
                    & models.Q(yes_no__isnull=True)
                )
                | (
                    models.Q(category__isnull=True)
                    & models.Q(number__isnull=True)
                    & models.Q(yes_no__isnull=False)
                ),
                name="one_value",
            ),
        ]

    @property
    def value(self) -> str | float | bool:
        if self.category_id is not None:
            return self.category.name  # type: ignore[union-attr]
        if self.number is not None:
            return self.number
        assert self.yes_no is not None
        return self.yes_no

    def clean(self) -> None:
        if self.variable_id is None:
            return  # the field's own check says so
        variable = self.variable
        kind = Variable.Kind(variable.kind)
        given = {
            "category": self.category_id is not None,
            "number": self.number is not None,
            "yes_no": self.yes_no is not None,
        }
        wanted = _VALUE_FIELD[kind]
        if not given[wanted] or any(is_given for f, is_given in given.items() if f != wanted):
            raise ValidationError(
                f"{variable.name} is a {kind.label} variable: its value is "
                f"{_VALUE_WORDS[kind]}, and nothing else"
            )
        if self.category is not None and self.category.variable_id != variable.pk:
            raise ValidationError(
                f"{self.category.name!r} is not one of {variable.name}'s categories"
            )
        if self.number is not None and not math.isfinite(self.number):
            raise ValidationError("a number must be finite")

    def __str__(self) -> str:
        return f"{self.subject}: {self.variable} = {self.value}"


class Feature(models.Model):
    """A derived configuration: a kind from `features.KINDS` and its settings, which the kind
    checks and keeps in a standard form."""

    name = models.CharField(max_length=200, unique=True)
    kind = models.CharField(max_length=50)
    settings = models.JSONField(default=dict, help_text="The kind's settings, as it checks them.")
    description = models.TextField(blank=True)

    def clean(self) -> None:
        self.settings = features.kind(self.kind).check(self.settings)
        if self.pk is None or not AnalysisRun.objects.filter(hypothesis__feature=self).exists():
            return
        stored = Feature.objects.get(pk=self.pk)
        if (stored.kind, stored.settings) != (self.kind, self.settings):
            raise ValidationError(
                f"{self.name} is used by a hypothesis with results, so it can't change"
            )

    def describe(self) -> str:
        return features.KINDS[self.kind].describe(self.settings)

    def value(self, chart: Chart) -> features.Value:
        return features.KINDS[self.kind].value(self.settings, chart)

    def __str__(self) -> str:
        return self.name


class Hypothesis(models.Model):
    """A question put to a calculation run: is a feature's value tied to a group of subjects?"""

    LOCKED = ("name", "statement", "feature", "variable", "category", "run", "analysis", "settings")

    name = models.CharField(max_length=200, unique=True)
    statement = models.TextField(help_text="The hypothesis in words, as it was registered.")
    feature = models.ForeignKey(Feature, on_delete=models.PROTECT, related_name="hypotheses")
    variable = models.ForeignKey(Variable, on_delete=models.PROTECT, related_name="hypotheses")
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="hypotheses",
        help_text="The group, for a category variable (a yes/no one's is yes).",
    )
    run = models.ForeignKey(CalculationRun, on_delete=models.PROTECT, related_name="hypotheses")
    analysis = models.CharField(max_length=50)
    settings = models.JSONField(
        default=dict, help_text="The analysis's settings, as it checks them."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "hypotheses"

    def clean(self) -> None:
        chosen = analyses.analysis(self.analysis)
        self.settings = chosen.check(self.settings)
        if self.feature_id is not None:
            value_kind = features.kind(self.feature.kind).value_kind
            if value_kind not in chosen.feature_values:
                raise ValidationError(
                    f"{chosen.name} needs a yes/no feature; {self.feature.name} is a "
                    f"{self.feature.kind} feature"
                )
        if self.variable_id is not None:
            self._check_group(chosen)
        if self.run_id is not None and self.run.status != CalculationRun.Status.FINISHED:
            raise ValidationError(f"run {self.run.pk} is {self.run.status}, not finished")
        if self.pk is not None and self.results.exists():
            stored = Hypothesis.objects.get(pk=self.pk)
            if any(_field(stored, name) != _field(self, name) for name in self.LOCKED):
                raise ValidationError(f"{self.name} has results, so it can't change")

    def _check_group(self, chosen: analyses.Analysis) -> None:
        variable = self.variable
        kind = Variable.Kind(variable.kind)
        if kind not in chosen.variable_kinds:
            raise ValidationError(
                f"{chosen.name} needs a category or yes/no variable; {variable.name} is a "
                f"{kind.label} variable"
            )
        if kind == Variable.Kind.CATEGORY:
            if self.category is None:
                raise ValidationError(f"name which of {variable.name}'s categories is the group")
            if self.category.variable_id != variable.pk:
                raise ValidationError(
                    f"{self.category.name!r} is not one of {variable.name}'s categories"
                )
        elif self.category is not None:
            raise ValidationError(
                f"{variable.name} is a yes/no variable, so its group is the subjects with yes; "
                "name no category"
            )

    def __str__(self) -> str:
        return self.name


def _field(hypothesis: Hypothesis, name: str) -> object:
    """A field's value, by id for a relation, so an unsaved change compares with the stored."""
    return hypothesis.serializable_value(name)


class AnalysisRun(models.Model):
    """One result of a hypothesis: its inputs, what was left out and why, the analysis's
    result, and the code and engine that produced it. Never changed or deleted."""

    hypothesis = models.ForeignKey(Hypothesis, on_delete=models.PROTECT, related_name="results")
    ran_at = models.DateTimeField(auto_now_add=True)
    inputs = models.JSONField(help_text="Each subject's record, feature value and group.")
    excluded = models.JSONField(help_text="The records left out, by reason.")
    result = models.JSONField()
    analysis_sha256 = models.CharField(max_length=64, help_text="analyses.py's SHA-256.")
    engine_version = models.CharField(max_length=50)

    def __str__(self) -> str:
        return f"result {self.pk} of {self.hypothesis}"


def _microseconds(duration: timedelta | None) -> int | None:
    return None if duration is None else duration // timedelta(microseconds=1)


def _duration(microseconds: int | None) -> timedelta | None:
    return None if microseconds is None else timedelta(microseconds=microseconds)
