"""The hypotheses' pages: the list, adding and editing from forms, running, and each result.

A hypothesis's form is the common part (name, statement, feature, group, run), offering only
what its analysis takes, plus the analysis's own settings form. A group is one choice:
`category-<id>` for one of a category variable's categories, `yes-<id>` for a yes/no variable's
yes. Running is a POST; a refusal is shown with a 422 and records nothing. Once a hypothesis
has a result it's shown as locked and its edit form is refused. Every result is shown, and its
inputs download as CSV, as stored.
"""

import csv
import io
from typing import Any

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from research_platform import analyses, features
from research_platform.hypotheses import AnalysisRefused, run_hypothesis
from research_platform.models import (
    AnalysisRun,
    CalculationRun,
    Category,
    Feature,
    Hypothesis,
    Variable,
)


class RunChoice(forms.ModelChoiceField[CalculationRun]):
    def label_from_instance(self, obj: CalculationRun) -> str:
        return f"run {obj.pk} of {obj.dataset.name}"


class HypothesisForm(forms.Form):
    """What every hypothesis names, offering only what `analysis` takes."""

    name = forms.CharField(max_length=200)
    statement = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), help_text="The hypothesis in words."
    )
    feature = forms.ModelChoiceField(queryset=Feature.objects.none())
    group = forms.ChoiceField(label="Outcome group")
    run = RunChoice(queryset=CalculationRun.objects.none(), label="Calculation run")

    def __init__(self, analysis: analyses.Analysis, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        kinds = [
            k for k, kind in features.KINDS.items() if kind.value_kind in analysis.feature_values
        ]
        feature_field = self.fields["feature"]
        assert isinstance(feature_field, forms.ModelChoiceField)
        feature_field.queryset = Feature.objects.filter(kind__in=kinds).order_by("name")
        run_field = self.fields["run"]
        assert isinstance(run_field, forms.ModelChoiceField)
        run_field.queryset = (
            CalculationRun.objects.filter(status=CalculationRun.Status.FINISHED)
            .select_related("dataset")
            .order_by("-pk")
        )
        group_field = self.fields["group"]
        assert isinstance(group_field, forms.ChoiceField)
        group_field.choices = _groups(analysis)

    def group_of(self) -> tuple[Variable, Category | None]:
        kind, pk = self.cleaned_data["group"].split("-", 1)
        if kind == "category":
            category = Category.objects.select_related("variable").get(pk=int(pk))
            return category.variable, category
        return Variable.objects.get(pk=int(pk)), None


def _groups(analysis: analyses.Analysis) -> list[tuple[str, str]]:
    choices = []
    variables = Variable.objects.filter(kind__in=analysis.variable_kinds).order_by("name")
    for variable in variables.prefetch_related("categories"):
        if variable.kind == Variable.Kind.CATEGORY:
            for category in variable.categories.all():
                choices.append((f"category-{category.pk}", f"{variable.name}: {category.name}"))
        else:
            choices.append((f"yes-{variable.pk}", f"{variable.name}: yes"))
    return choices


def _group_label(hypothesis: Hypothesis) -> str:
    """The group as the form offers it, such as "profession: athlete"."""
    category = hypothesis.category
    return f"{hypothesis.variable.name}: {category.name if category else 'yes'}"


def _group_value(hypothesis: Hypothesis) -> str:
    if hypothesis.category_id is not None:
        return f"category-{hypothesis.category_id}"
    return f"yes-{hypothesis.variable_id}"


@login_required
def hypotheses_list(request: HttpRequest) -> HttpResponse:
    rows = []
    for each in Hypothesis.objects.order_by("name").select_related(
        "feature", "variable", "category", "run__dataset"
    ):
        results = list(each.results.order_by("-pk")[:1])
        latest = results[0] if results else None
        summary = ""
        if latest is not None:
            summary = analyses.analysis(each.analysis).summarize(latest.result, each.settings)
        rows.append(
            {
                "hypothesis": each,
                "group": _group_label(each),
                "count": each.results.count(),
                "latest": summary,
            }
        )
    context = {"rows": rows, "total": AnalysisRun.objects.count()}
    return render(request, "research_platform/hypotheses.html", context)


@login_required
def hypothesis_new(request: HttpRequest) -> HttpResponse:
    context: dict[str, object] = {"analyses": analyses.ANALYSES.values()}
    name = request.GET.get("analysis")
    if name is None:
        return render(request, "research_platform/hypothesis_form.html", context)
    try:
        chosen = analyses.analysis(name)
    except ValidationError as error:
        context["error"] = error.messages[0]
        return render(request, "research_platform/hypothesis_form.html", context, status=400)
    return _form(request, chosen, Hypothesis(analysis=chosen.name), context)


@login_required
def hypothesis_edit(request: HttpRequest, pk: int) -> HttpResponse:
    chosen = get_object_or_404(Hypothesis, pk=pk)
    analysis = analyses.analysis(chosen.analysis)
    context: dict[str, object] = {"hypothesis": chosen}
    if chosen.results.exists():
        context["error"] = f"{chosen.name} has results, so it can't change"
        return render(request, "research_platform/hypothesis_form.html", context, status=400)
    initial = {
        "name": chosen.name,
        "statement": chosen.statement,
        "feature": chosen.feature_id,
        "group": _group_value(chosen),
        "run": chosen.run_id,
    }
    return _form(request, analysis, chosen, context, initial, chosen.settings)


def _form(
    request: HttpRequest,
    analysis: analyses.Analysis,
    hypothesis: Hypothesis,
    context: dict[str, object],
    initial: dict[str, object] | None = None,
    settings_initial: dict[str, object] | None = None,
) -> HttpResponse:
    data = request.POST if request.method == "POST" else None
    form = HypothesisForm(analysis, data, initial=initial)
    settings_form = analysis.settings_form(data, initial=settings_initial)
    context |= {"analysis": analysis, "form": form, "settings_form": settings_form}
    if data is None:
        return render(request, "research_platform/hypothesis_form.html", context)
    if form.is_valid() and settings_form.is_valid():
        variable, category = form.group_of()
        hypothesis.name = form.cleaned_data["name"]
        hypothesis.statement = form.cleaned_data["statement"]
        hypothesis.feature = form.cleaned_data["feature"]
        hypothesis.variable, hypothesis.category = variable, category
        hypothesis.run = form.cleaned_data["run"]
        hypothesis.settings = settings_form.settings()  # type: ignore[attr-defined]
        try:
            hypothesis.full_clean()
        except ValidationError as error:
            for field, errors in error.message_dict.items():  # "__all__" for clean()'s
                form.add_error(field if field in form.fields else None, errors)
        else:
            hypothesis.save()
            return redirect("hypothesis", pk=hypothesis.pk)
    return render(request, "research_platform/hypothesis_form.html", context, status=400)


@login_required
def hypothesis(request: HttpRequest, pk: int) -> HttpResponse:
    chosen = get_object_or_404(
        Hypothesis.objects.select_related("feature", "variable", "category", "run__dataset"),
        pk=pk,
    )
    return render(request, "research_platform/hypothesis.html", _page(chosen))


def _page(chosen: Hypothesis) -> dict[str, object]:
    analysis = analyses.analysis(chosen.analysis)
    results = [
        {"run": each, "shown": analysis.present(each.result, chosen.settings)}
        for each in chosen.results.order_by("-pk")
    ]
    return {
        "hypothesis": chosen,
        "analysis": analysis,
        "described": analysis.describe(chosen.settings),
        "feature_described": chosen.feature.describe(),
        "group": _group_label(chosen),
        "results": results,
        "locked": bool(results),
    }


@login_required
@require_POST
def hypothesis_run(request: HttpRequest, pk: int) -> HttpResponse:
    chosen = get_object_or_404(Hypothesis, pk=pk)
    try:
        run_hypothesis(chosen)
    except AnalysisRefused as error:
        context = _page(chosen) | {"refused": str(error)}
        return render(request, "research_platform/hypothesis.html", context, status=422)
    except ValidationError as error:
        context = _page(chosen) | {"refused": "; ".join(error.messages)}
        return render(request, "research_platform/hypothesis.html", context, status=400)
    return redirect("hypothesis", pk=chosen.pk)


@login_required
def result_inputs_csv(request: HttpRequest, pk: int) -> HttpResponse:
    result = get_object_or_404(AnalysisRun, pk=pk)
    buffer = io.StringIO()
    writer = csv.writer(buffer)  # RFC 4180: \r\n line endings, quoted only where needed
    writer.writerow(["record_id", "subject", "feature", "in_group"])
    for row in result.inputs:
        writer.writerow(
            [
                row["record"],
                row["subject"],
                "true" if row["feature"] else "false",
                "true" if row["in_group"] else "false",
            ]
        )
    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="result-{result.pk}-inputs.csv"'
    return response
