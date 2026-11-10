"""The admin: where subjects, sources, records and datasets are entered.

Runs, outcomes and stored charts are what the engine produced, so they can be looked at but not
added, changed or deleted here. A record is validated by the engine when it's saved (`Record.
clean()`), and its page links to its chart. Two records chosen in the list open their
comparison. A dataset's page links to its runs, where a run can be started. Variables and
their values are entered here too: on a variable's page, or a subject's. Records imported
from AstroChart Bridge (`import_charts`) keep their files, which can be read but not changed.
"""

from typing import Any

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString

from research_platform.models import (
    AnalysisRun,
    CalculationRun,
    Category,
    Dataset,
    Hypothesis,
    ImportedChart,
    Observation,
    Record,
    RunOutcome,
    Source,
    StoredChart,
    Subject,
    Variable,
)


class ObservationInline(admin.TabularInline[Observation, Any]):
    """Values, each checked against its variable when saved."""

    model = Observation
    extra = 0
    autocomplete_fields = ("variable", "subject", "source")


class SubjectObservationInline(ObservationInline):
    fields = ("variable", "category", "number", "yes_no", "source", "notes")


class VariableObservationInline(ObservationInline):
    fields = ("subject", "category", "number", "yes_no", "source", "notes")


class CategoryInline(admin.TabularInline[Category, Variable]):
    model = Category
    extra = 0


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin[Subject]):
    list_display = ("label",)
    search_fields = ("label", "notes")
    inlines = (SubjectObservationInline,)


@admin.register(Variable)
class VariableAdmin(admin.ModelAdmin[Variable]):
    """A variable, its categories if it's a category variable, and every subject's value."""

    list_display = ("name", "kind", "unit")
    list_filter = ("kind",)
    search_fields = ("name", "description")
    inlines = (CategoryInline, VariableObservationInline)


@admin.register(Observation)
class ObservationAdmin(admin.ModelAdmin[Observation]):
    list_display = ("variable", "subject", "value", "source")
    list_filter = ("variable",)
    search_fields = ("subject__label", "variable__name")
    autocomplete_fields = ("variable", "subject", "source")


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin[Source]):
    list_display = ("citation", "reliability", "retrieved_on")
    search_fields = ("citation", "notes")


@admin.register(Record)
class RecordAdmin(admin.ModelAdmin[Record]):
    list_display = ("subject", "reading", "time_kind", "zone", "source")
    list_filter = ("time_kind", "time_resolution")
    search_fields = ("subject__label", "reading", "zone")
    readonly_fields = ("imported_from", "chart", "sensitivity", "eclipses", "parans")
    actions = ("compare_records",)

    @admin.display(description="Imported from")
    def imported_from(self, record: Record) -> SafeString | str:
        imported = ImportedChart.objects.filter(record_id=record.pk).first() if record.pk else None
        if imported is None:
            return "Entered here."
        url = reverse("admin:research_platform_importedchart_change", args=[imported.pk])
        return format_html(
            '<a href="{}">{}</a>, from AstroChart Bridge: site {}, read from {}, UTC from {},'
            " imported {}",
            url,
            imported.file_name,
            imported.site or "not given",
            imported.via or "not given",
            imported.utc_basis or "not given",
            f"{imported.imported_at:%Y-%m-%d}",
        )

    @admin.display(description="Chart")
    def chart(self, record: Record) -> SafeString | str:
        if record.pk is None:
            return "Save the record to see its chart."
        return format_html('<a href="{}">Chart</a>', reverse("record-chart", args=[record.pk]))

    @admin.display(description="Birth-time sensitivity")
    def sensitivity(self, record: Record) -> SafeString | str:
        if record.pk is None:
            return "Save the record to sweep its birth time."
        url = reverse("record-sensitivity", args=[record.pk])
        return format_html('<a href="{}">Birth-time sensitivity</a>', url)

    @admin.display(description="Nearest eclipses")
    def eclipses(self, record: Record) -> SafeString | str:
        if record.pk is None:
            return "Save the record to find its eclipses."
        url = reverse("record-eclipses", args=[record.pk])
        return format_html('<a href="{}">Nearest eclipses</a>', url)

    @admin.display(description="Parans")
    def parans(self, record: Record) -> SafeString | str:
        if record.pk is None:
            return "Save the record to find its parans."
        return format_html('<a href="{}">Parans</a>', reverse("record-parans", args=[record.pk]))

    @admin.action(description="Compare the two selected records")
    def compare_records(
        self, request: HttpRequest, queryset: QuerySet[Record]
    ) -> HttpResponse | None:
        """Opens B minus A, with the older record (the lower id) as A."""
        records = list(queryset.order_by("pk"))
        if len(records) != 2:
            self.message_user(
                request, "Choose exactly two records to compare.", level=messages.ERROR
            )
            return None
        return HttpResponseRedirect(reverse("record-compare", args=[r.pk for r in records]))


@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin[Dataset]):
    list_display = ("name",)
    filter_horizontal = ("records",)
    readonly_fields = ("runs",)

    @admin.display(description="Runs")
    def runs(self, dataset: Dataset) -> SafeString | str:
        if dataset.pk is None:
            return "Save the dataset to run it."
        url = reverse("dataset", args=[dataset.pk])
        return format_html('<a href="{}">Its runs, and starting one</a>', url)


class ProducedByTheEngine[M: Any](admin.ModelAdmin[M]):
    """Shown, never added, changed or deleted by hand."""

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: M | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: M | None = None) -> bool:
        return False


@admin.register(CalculationRun)
class CalculationRunAdmin(ProducedByTheEngine[CalculationRun]):
    list_display = ("pk", "dataset", "status", "computed", "reused", "refused", "started_at")
    list_filter = ("status",)


@admin.register(RunOutcome)
class RunOutcomeAdmin(ProducedByTheEngine[RunOutcome]):
    list_display = ("run", "record", "chart", "refusal_class")
    list_filter = ("refusal_class",)


@admin.register(ImportedChart)
class ImportedChartAdmin(ProducedByTheEngine[ImportedChart]):
    """The files records were imported from, as they were (`import_charts`)."""

    list_display = ("file_name", "record", "site", "via", "utc_basis", "imported_at")
    list_filter = ("via", "site", "utc_basis")
    search_fields = ("file_name", "record__subject__label")


@admin.register(StoredChart)
class StoredChartAdmin(ProducedByTheEngine[StoredChart]):
    list_display = ("calculation_key", "stored_at")


@admin.register(Hypothesis)
class HypothesisAdmin(admin.ModelAdmin[Hypothesis]):
    """Hypotheses until they have pages of their own; checked on save, locked once they have a
    result. `run_hypothesis NAME` runs one."""

    list_display = ("name", "feature", "variable", "category", "run", "analysis")
    search_fields = ("name", "statement")


@admin.register(AnalysisRun)
class AnalysisRunAdmin(ProducedByTheEngine[AnalysisRun]):
    list_display = ("pk", "hypothesis", "ran_at", "engine_version")
    list_filter = ("hypothesis",)
