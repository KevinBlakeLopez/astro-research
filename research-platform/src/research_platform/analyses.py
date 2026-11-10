"""The analysis registry: the tests a hypothesis can name, written as code by the platform's
maintainers, and chosen and set up by researchers.

Each analysis says what it takes (the kinds of feature value and of variable), checks its
settings and keeps them in a standard form, works out a result from a hypothesis's inputs, and
sums the result up in words. A result's record carries this file's SHA-256, so it names the code
that produced it.

The first analysis is Fisher's exact test on the 2x2 table of group x feature: rows (group,
rest), columns (feature yes, feature no). The p-value is exact, a fraction worked out with
`math.comb`, so it comes out the same every time and needs no random seed:

- greater: the feature more common in the group, P(X >= a);
- less: P(X <= a);
- two-sided: the probability of every table no more likely than the observed one, compared
  exactly (R's fisher.test allows a relative tolerance of 1e-7 there instead).

Here X is the group's count with the feature, which is hypergeometric given the margins. A
result is at or below the significance level when p <= alpha, with alpha read as written: 0.05
is exactly 1/20.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, TypeGuard

from django.core.exceptions import ValidationError

Settings = dict[str, Any]
Inputs = list[dict[str, Any]]  # one per subject: record, subject, feature, in_group
Result = dict[str, Any]
Table = tuple[tuple[int, int], tuple[int, int]]
ALTERNATIVES = ("greater", "less", "two-sided")


def fisher_exact(table: Table, alternative: str) -> Fraction:
    """The exact p-value of Fisher's test on ((a, b), (c, d)): rows group and rest, columns
    feature yes and no."""
    (a, b), (c, d) = table
    if min(a, b, c, d) < 0:
        raise ValueError(f"a table's counts can't be negative: {table}")
    n, row, column = a + b + c + d, a + b, a + c
    # The ways to draw the group's `row` subjects with x of the `column` that have the feature;
    # math.comb is 0 for a draw that can't happen, so those add nothing.
    ways = {
        x: math.comb(column, x) * math.comb(n - column, row - x)
        for x in range(min(row, column) + 1)
    }
    if alternative == "greater":
        extreme = sum(w for x, w in ways.items() if x >= a)
    elif alternative == "less":
        extreme = sum(w for x, w in ways.items() if x <= a)
    elif alternative == "two-sided":
        extreme = sum(w for w in ways.values() if w <= ways[a])
    else:
        raise ValueError(f"alternative {alternative!r} is not one of {', '.join(ALTERNATIVES)}")
    return Fraction(extreme, math.comb(n, row))


def odds_ratio(table: Table) -> float | None:
    """(a d) / (b c): infinite when only b c is 0, and undefined (None) when both are."""
    (a, b), (c, d) = table
    if b * c == 0:
        return None if a * d == 0 else math.inf
    return a * d / (b * c)


def _is_number(value: object) -> TypeGuard[float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _check_fisher(settings: Settings) -> Settings:
    alternative = settings.get("alternative")
    if alternative not in ALTERNATIVES:
        raise ValidationError(
            f"alternative {alternative!r} is not one of {', '.join(ALTERNATIVES)}"
        )
    alpha = settings.get("alpha")
    if not _is_number(alpha) or not 0 < alpha < 1:
        raise ValidationError("the significance level must be a number between 0 and 1")
    return {"alternative": alternative, "alpha": float(alpha)}


def table_of(inputs: Inputs) -> Table:
    """The 2x2 table of the inputs: rows group and rest, columns feature yes and no."""

    def count(in_group: bool, feature: bool) -> int:
        return sum(row["in_group"] is in_group and row["feature"] is feature for row in inputs)

    return ((count(True, True), count(True, False)), (count(False, True), count(False, False)))


def _run_fisher(inputs: Inputs, settings: Settings) -> Result:
    table = table_of(inputs)
    p = fisher_exact(table, settings["alternative"])
    return {
        "table": [list(row) for row in table],
        "p_value": str(p),
        "below_alpha": p <= Fraction(repr(settings["alpha"])),
    }


def _summarize_fisher(result: Result, settings: Settings) -> str:
    p = Fraction(result["p_value"])
    level = "at or below" if result["below_alpha"] else "above"
    return f"p = {float(p):.4g} ({p}), {level} {settings['alpha']:g}"


@dataclass(frozen=True)
class Analysis:
    name: str
    explanation: str
    feature_values: frozenset[str]  # the kinds of feature value it takes, as features.Kind's
    variable_kinds: frozenset[str]  # the kinds of variable it takes, as Variable.Kind's values
    check: Callable[[Settings], Settings]
    run: Callable[[Inputs, Settings], Result]
    summarize: Callable[[Result, Settings], str]


ANALYSES = {
    "fisher-exact": Analysis(
        name="fisher-exact",
        explanation="Fisher's exact test: is a yes/no feature more (or less) common in a group?",
        feature_values=frozenset({"yes/no"}),
        variable_kinds=frozenset({"category", "yes-no"}),
        check=_check_fisher,
        run=_run_fisher,
        summarize=_summarize_fisher,
    ),
}


def analysis(name: str) -> Analysis:
    """The analysis named, or ValidationError saying which there are."""
    if name not in ANALYSES:
        raise ValidationError(f"analysis {name!r} is not one of {', '.join(ANALYSES)}")
    return ANALYSES[name]
