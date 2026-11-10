"""Feature kinds: what a derived configuration can be, and how its value comes from a chart.

The kinds are a registry written as code: each checks its settings and puts them in a standard
form, describes them in words, gives a chart's value, and has a form for entering them, so
researchers fill in a form rather than write code. A feature is a kind and its settings
(`models.Feature`). The first two:

- contact (yes/no): a point, other points, aspect angles and an orb. Yes when, for any other
  point and angle, the ecliptic separation (as in the pairs table) is within the orb of the
  angle, the orb's edge included;
- sign (a category): the point's tropical zodiac sign, its longitude in 30-degree steps from
  0 Aries.

A point is one of a chart's 17: the bodies, the lunar points, the Ascendant, MC and Vertex.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeGuard

from django import forms
from django.core.exceptions import ValidationError

from astro_research.chart import Chart
from astro_research.measurements import ChartAngle
from astro_research.positions import ALL_POINTS
from research_platform.chart_settings import InvalidSettings, aspect_angles

POINTS = (*(point.value for point in ALL_POINTS), *(angle.value for angle in ChartAngle))
SIGNS = (
    "aries",
    "taurus",
    "gemini",
    "cancer",
    "leo",
    "virgo",
    "libra",
    "scorpio",
    "sagittarius",
    "capricorn",
    "aquarius",
    "pisces",
)
Settings = dict[str, Any]
Value = bool | str


def sign_of(longitude: float) -> str:
    return SIGNS[int(longitude // 30) % 12]


class FeatureForm(forms.Form):
    """What every kind's form asks first."""

    name = forms.CharField(max_length=200)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def settings(self) -> Settings:
        raise NotImplementedError


class ContactForm(FeatureForm):
    point = forms.ChoiceField(choices=[(p, p) for p in POINTS])
    others = forms.MultipleChoiceField(
        choices=[(p, p) for p in POINTS],
        widget=forms.CheckboxSelectMultiple,
        label="To any of",
    )
    angles = forms.CharField(
        label="At any of the aspect angles",
        help_text="Degrees and/or major, octile, e.g. 0,180",
    )
    orb = forms.FloatField(label="Within an orb of (degrees)")

    def clean_angles(self) -> list[float]:
        try:
            return list(aspect_angles(self.cleaned_data["angles"]))
        except InvalidSettings as error:
            raise ValidationError(str(error)) from None

    def settings(self) -> Settings:
        data = self.cleaned_data
        return {
            "point": data["point"],
            "others": data["others"],
            "angles": data["angles"],
            "orb": data["orb"],
        }


class SignForm(FeatureForm):
    point = forms.ChoiceField(choices=[(p, p) for p in POINTS])

    def settings(self) -> Settings:
        return {"point": self.cleaned_data["point"]}


@dataclass(frozen=True)
class Kind:
    name: str
    explanation: str  # what the kind is, for choosing one
    check: Callable[[Settings], Settings]  # the settings in standard form, or ValidationError
    describe: Callable[[Settings], str]
    value: Callable[[Settings, Chart], Value]
    possible: tuple[Value, ...]  # every value it can have, in the order they're counted
    form: type[FeatureForm]


def _point(name: object) -> str:
    if name not in POINTS:
        raise ValidationError(f"{name!r} is not one of the chart's points")
    assert isinstance(name, str)
    return name


def _check_contact(settings: Settings) -> Settings:
    point = _point(settings.get("point"))
    others = settings.get("others") or []
    if not others:
        raise ValidationError("name at least one other point")
    chosen = {_point(other) for other in others}
    if point in chosen:
        raise ValidationError("a point can't be in contact with itself")
    angles = settings.get("angles") or []
    if not angles:
        raise ValidationError("name at least one aspect angle")
    for angle in angles:
        if not _is_number(angle):
            raise ValidationError(f"angle {angle!r} is not a number")
        if not 0 <= angle <= 180:
            raise ValidationError(f"angle {angle:g} is outside 0° to 180°")
    orb = settings.get("orb")
    if not _is_number(orb) or not math.isfinite(orb) or orb < 0:
        raise ValidationError("the orb must be a number of degrees, 0 or more")
    return {
        "point": point,
        "others": [p for p in POINTS if p in chosen],  # in the chart's order
        "angles": sorted({float(angle) for angle in angles}),
        "orb": float(orb),
    }


def _is_number(value: object) -> TypeGuard[float]:
    """An int or a float, but not a bool (which Python counts as an int)."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def _describe_contact(settings: Settings) -> str:
    others = " or ".join(settings["others"])
    angles = " or ".join(f"{angle:g}°" for angle in settings["angles"])
    return f"{settings['point']} to {others} at {angles}, within {settings['orb']:g}°"


def _contact_value(settings: Settings, chart: Chart) -> bool:
    separations = {
        frozenset((pair.a.point.value, pair.b.point.value)): pair.separation for pair in chart.pairs
    }
    point = settings["point"]
    return any(
        abs(separations[frozenset((point, other))] - angle) <= settings["orb"]
        for other in settings["others"]
        for angle in settings["angles"]
    )


def _check_sign(settings: Settings) -> Settings:
    return {"point": _point(settings.get("point"))}


def _sign_value(settings: Settings, chart: Chart) -> str:
    point = settings["point"]
    return sign_of(next(p.longitude for p in chart.points if p.point.value == point))


KINDS = {
    "contact": Kind(
        name="contact",
        explanation="Yes or no: a point within an orb of an aspect angle to any of other points.",
        check=_check_contact,
        describe=_describe_contact,
        value=_contact_value,
        possible=(True, False),
        form=ContactForm,
    ),
    "sign": Kind(
        name="sign",
        explanation="A category: a point's tropical zodiac sign.",
        check=_check_sign,
        describe=lambda settings: f"{settings['point']}'s sign",
        value=_sign_value,
        possible=SIGNS,
        form=SignForm,
    ),
}


def kind(name: str) -> Kind:
    """The kind named, or ValidationError saying which there are."""
    if name not in KINDS:
        raise ValidationError(f"kind {name!r} is not one of {', '.join(KINDS)}")
    return KINDS[name]
