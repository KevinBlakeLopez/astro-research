"""Template filters for measurements, formatted as Python formats them."""

from django import template

register = template.Library()


@register.filter
def fixed(value: float, places: int = 6) -> str:
    """`value` to `places` decimals, as f-strings round (not Django's floatformat, which rounds
    the decimal repr half up and can differ in the last place)."""
    return f"{value:.{int(places)}f}"


@register.filter
def arcsec(degrees: float, places: int = 3) -> str:
    """A change in degrees, in arcseconds to `places` decimals (3: one milliarcsecond)."""
    return f"{degrees * 3600:.{int(places)}f}"
