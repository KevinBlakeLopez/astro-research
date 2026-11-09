"""The admin: where subjects, sources, records and datasets are entered.

Runs, outcomes and stored charts are what the engine produced, so they can be looked at but not
added, changed or deleted here. A record is validated by the engine when it's saved (`Record.
clean()`), and its page links to its chart.
"""

from typing import Any

from django.contrib import admin
from django.http import HttpRequest
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
    readonly_fields = ("chart",)

    @admin.display(description="Chart")
    def chart(self, record: Record) -> SafeString | str:
        if record.pk is None:
            return "Save the record to see its chart."
        return format_html('<a href="{}">Chart</a>', reverse("record-chart", args=[record.pk]))


@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin[Dataset]):
    list_display = ("name",)
    filter_horizontal = ("records",)


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
