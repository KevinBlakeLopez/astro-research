"""Getting around: the home page and the records page.

The home page shows the platform's places, each with how many it holds, and the latest runs and
hypothesis results; with no records yet, it says where to start. The records page lists every
record by subject, 100 at a time, with links to its chart and the datasets holding it, and
finds records by part of a subject's label.

Matching and ordering ignore case, accented letters included, so they're done in Python with
`casefold`: SQLite's own matching treats "é" and "É" as different (observed), and its order and
PostgreSQL's differ. Only the page's own records are then read in full.
"""

from dataclasses import dataclass
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from research_platform import analyses
from research_platform.models import (
    AnalysisRun,
    CalculationRun,
    Dataset,
    Feature,
    Hypothesis,
    Record,
)

LATEST = 5
RECORDS_PER_PAGE = 100


@dataclass(frozen=True)
class Place:
    name: str  # its route
    label: str
    count: int


@dataclass(frozen=True)
class LatestResult:
    result: AnalysisRun
    summary: str


@login_required
def home(request: HttpRequest) -> HttpResponse:
    return render(request, "research_platform/home.html", home_context())


def home_context() -> dict[str, object]:
    """What the home page shows."""
    places = [
        Place("records", "Records", Record.objects.count()),
        Place("datasets", "Datasets", Dataset.objects.count()),
        Place("features", "Features", Feature.objects.count()),
        Place("hypotheses", "Hypotheses", Hypothesis.objects.count()),
    ]
    results = AnalysisRun.objects.select_related("hypothesis").order_by("-pk")[:LATEST]
    return {
        "places": places,
        "empty": places[0].count == 0,
        "runs": CalculationRun.objects.select_related("dataset").order_by("-pk")[:LATEST],
        "results": [
            LatestResult(
                each,
                analyses.analysis(each.hypothesis.analysis).summarize(
                    each.result, each.hypothesis.settings
                ),
            )
            for each in results
        ],
    }


@login_required
def records(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()
    needle = query.casefold()
    labelled = Record.objects.values_list("pk", "subject__label")
    ordered = sorted((label.casefold(), pk) for pk, label in labelled if needle in label.casefold())
    page = Paginator([pk for _, pk in ordered], RECORDS_PER_PAGE).get_page(request.GET.get("page"))
    shown = (
        Record.objects.select_related("subject", "source")
        .prefetch_related(Prefetch("datasets", queryset=Dataset.objects.order_by("name")))
        .in_bulk(list(page.object_list))
    )

    def address(number: int) -> str:
        return "?" + urlencode({"q": query, "page": number} if query else {"page": number})

    context = {
        "page": page,
        "records": [shown[pk] for pk in page.object_list],
        "query": query,
        "total": page.paginator.count,
        "previous": address(page.previous_page_number()) if page.has_previous() else None,
        "next": address(page.next_page_number()) if page.has_next() else None,
    }
    return render(request, "research_platform/records.html", context)
