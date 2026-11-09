"""Template filters for measurements, formatted as Python formats them."""

from django import template

register = template.Library()


@register.filter
def fixed(value: float, places: int = 6) -> str:
    """`value` to `places` decimals, as f-strings round (not Django's floatformat, which rounds
    the decimal repr half up and can differ in the last place)."""
    return f"{value:.{int(places)}f}"
