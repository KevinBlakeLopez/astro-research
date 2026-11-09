"""The admin: where subjects, sources, records and datasets are entered.

Runs, outcomes and stored charts are what the engine produced, so they can be looked at but not
added, changed or deleted here. A record is validated by the engine when it's saved (`Record.
clean()`), and its page links to its chart. Two records chosen in the list open their
comparison. A dataset's page links to its runs, where a run can be started.
"""

from typing import Any

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString

from research_platform.models import (
    CalculationRun,
    Dataset,
    Record,
    RunOutcome,
    Source,
    StoredChart,
    Subject,
)


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin[Subject]):
    list_display = ("label",)
    search_fields = ("label", "notes")


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin[Source]):
    list_display = ("citation", "reliability", "retrieved_on")
    search_fields = ("citation", "notes")


@admin.register(Record)
class RecordAdmin(admin.ModelAdmin[Record]):
    list_display = ("subject", "reading", "time_kind", "zone", "source")
    list_filter = ("time_kind", "time_resolution")
    search_fields = ("subject__label", "reading", "zone")
    readonly_fields = ("chart", "sensitivity", "eclipses", "parans")
    actions = ("compare_records",)

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


@admin.register(StoredChart)
class StoredChartAdmin(ProducedByTheEngine[StoredChart]):
    list_display = ("calculation_key", "stored_at")
