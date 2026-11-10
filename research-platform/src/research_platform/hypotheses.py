"""Running a hypothesis: its inputs from the calculation run, its analysis, and the result kept.

The inputs are one row per subject of the run: the record, the subject's label, the feature's
value on the record's stored chart, and whether the subject's value of the variable puts it in
the group. Left out, by record id: refused records, which have no chart, and records of
subjects with no value of the variable. A subject with two records in the run is refused, since
counting it twice would weigh it double, and which record stands for it is the dataset's
decision; so is a stored document this engine can't read. A refusal records nothing.
"""

import hashlib
from collections import Counter
from pathlib import Path

from astro_research.chart import ENGINE_VERSION
from astro_research.result_format import InvalidChartDocument, chart_from_json
from research_platform import analyses, features
from research_platform.models import AnalysisRun, Hypothesis, Observation, Variable
from research_platform.runs_views import _stored


class AnalysisRefused(ValueError):
    """The hypothesis's inputs can't be put together; the message says why."""


def run_hypothesis(hypothesis: Hypothesis) -> AnalysisRun:
    """The hypothesis's analysis on its run, recorded with the inputs it used."""
    hypothesis.full_clean()
    inputs, excluded = collect_inputs(hypothesis)
    chosen = analyses.analysis(hypothesis.analysis)
    return AnalysisRun.objects.create(
        hypothesis=hypothesis,
        inputs=inputs,
        excluded=excluded,
        result=chosen.run(inputs, hypothesis.settings),
        analysis_sha256=hashlib.sha256(Path(analyses.__file__).read_bytes()).hexdigest(),
        engine_version=ENGINE_VERSION,
    )


def collect_inputs(hypothesis: Hypothesis) -> tuple[analyses.Inputs, dict[str, list[int]]]:
    run = hypothesis.run
    outcomes = list(run.outcomes.select_related("record__subject").order_by("record__pk"))
    records = Counter(outcome.record.subject_id for outcome in outcomes)
    for outcome in outcomes:
        if records[outcome.record.subject_id] > 1:
            raise AnalysisRefused(
                f"subject {outcome.record.subject.label!r} has "
                f"{records[outcome.record.subject_id]} records in run {run.pk}; this analysis "
                "needs one record per subject"
            )
    observed = {
        observation.subject_id: observation
        for observation in Observation.objects.filter(variable=hypothesis.variable)
    }
    refused = [outcome.record.pk for outcome in outcomes if outcome.chart_id is None]
    charted = [outcome for outcome in outcomes if outcome.chart_id is not None]
    missing = [o.record.pk for o in charted if o.record.subject_id not in observed]
    counted = [o for o in charted if o.record.subject_id in observed]
    values = _feature_values(hypothesis, [o.chart_id for o in counted if o.chart_id])
    inputs = [
        {
            "record": outcome.record.pk,
            "subject": outcome.record.subject.label,
            "feature": values[outcome.chart_id],
            "in_group": _in_group(hypothesis, observed[outcome.record.subject_id]),
        }
        for outcome in counted
        if outcome.chart_id
    ]
    return inputs, {"refused": refused, "missing": missing}


def _feature_values(hypothesis: Hypothesis, keys: list[str]) -> dict[str, features.Value]:
    """The feature's value on each stored chart, read a batch at a time, keeping only values."""
    values: dict[str, features.Value] = {}
    for key, document in _stored(list(dict.fromkeys(keys))):
        try:
            values[key] = hypothesis.feature.value(chart_from_json(document))
        except InvalidChartDocument as error:
            raise AnalysisRefused(
                f"stored chart {key} can't be read by this engine: {error}"
            ) from error
    return values


def _in_group(hypothesis: Hypothesis, observation: Observation) -> bool:
    if hypothesis.variable.kind == Variable.Kind.CATEGORY:
        return observation.category_id == hypothesis.category_id
    return observation.yes_no is True
