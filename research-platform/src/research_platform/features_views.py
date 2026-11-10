"""Features' pages: the list, adding one (its kind first, then the kind's form), and a feature's
values across a run.

A feature's values come from the run's stored charts when the page is asked for, a batch at a
time, and only the values are kept. Refused records have no value; they're counted, not listed.
A stored document this engine can't read shows why, with a 422.
"""

from collections import Counter

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from astro_research.result_format import InvalidChartDocument, chart_from_json
from research_platform import features
from research_platform.models import CalculationRun, Feature, Record
from research_platform.runs_views import _stored
from research_platform.views import Refusal


@login_required
def features_list(request: HttpRequest) -> HttpResponse:
    found = Feature.objects.order_by("name")
    return render(request, "research_platform/features.html", {"features": found})


@login_required
def feature_new(request: HttpRequest) -> HttpResponse:
    context: dict[str, object] = {"kinds": features.KINDS.values()}
    name = request.GET.get("kind")
    if name is None:
        return render(request, "research_platform/feature_new.html", context)
    try:
        chosen = features.kind(name)
    except ValidationError as error:
        context["error"] = error.messages[0]
        return render(request, "research_platform/feature_new.html", context, status=400)
    form = chosen.form(request.POST or None)
    context |= {"kind": chosen, "form": form}
    if request.method != "POST":
        return render(request, "research_platform/feature_new.html", context)
    if form.is_valid():
        feature = Feature(
            name=form.cleaned_data["name"],
            description=form.cleaned_data["description"],
            kind=chosen.name,
            settings=form.settings(),
        )
        try:
            feature.full_clean()
        except ValidationError as error:
            for field, errors in error.message_dict.items():  # "__all__" for clean()'s
                form.add_error(field if field in form.fields else None, errors)
        else:
            feature.save()
            return redirect("feature", pk=feature.pk)
    return render(request, "research_platform/feature_new.html", context, status=400)


@login_required
def feature(request: HttpRequest, pk: int) -> HttpResponse:
    chosen = get_object_or_404(Feature, pk=pk)
    finished = CalculationRun.objects.filter(status=CalculationRun.Status.FINISHED)
    context: dict[str, object] = {
        "feature": chosen,
        "kind": features.KINDS[chosen.kind],
        "runs": finished.select_related("dataset").order_by("-pk"),
    }
    run_text = request.GET.get("run")
    if run_text is None:
        return render(request, "research_platform/feature.html", context)
    try:
        run_pk = int(run_text)
    except ValueError:
        context["error"] = f"run {run_text!r} is not a run's number"
        return render(request, "research_platform/feature.html", context, status=400)
    run = get_object_or_404(CalculationRun.objects.select_related("dataset"), pk=run_pk)
    context["run"] = run
    try:
        rows = _values(chosen, run)
    except _Unreadable as unreadable:
        context["refusal"] = unreadable.refusal
        return render(request, "research_platform/feature.html", context, status=422)
    counted = Counter(value for _, value in rows)
    possible = features.KINDS[chosen.kind].possible
    context |= {
        "rows": [(record, _label(value)) for record, value in rows],
        "counts": " · ".join(f"{_label(value)} {counted[value]}" for value in possible),
        "refused": run.outcomes.filter(chart=None).count(),
    }
    return render(request, "research_platform/feature.html", context)


class _Unreadable(Exception):
    def __init__(self, refusal: Refusal) -> None:
        self.refusal = refusal


def _values(chosen: Feature, run: CalculationRun) -> list[tuple[Record, features.Value]]:
    """Each charted record of the run, in record order, with the feature's value."""
    outcomes = run.outcomes.select_related("record__subject").order_by("record__pk")
    charted = [(outcome.record, outcome.chart_id) for outcome in outcomes if outcome.chart_id]
    keys = list(dict.fromkeys(key for _, key in charted))
    by_key: dict[str, features.Value] = {}
    for key, document in _stored(keys):
        try:
            chart = chart_from_json(document)
        except InvalidChartDocument as error:
            message = f"stored chart {key} can't be read by this engine: {error}"
            raise _Unreadable(Refusal(type(error).__name__, message)) from error
        by_key[key] = chosen.value(chart)
    return [(record, by_key[key]) for record, key in charted]


def _label(value: features.Value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return value
