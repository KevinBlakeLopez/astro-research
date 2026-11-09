"""Chart settings from text, for the command's options and the pages' addresses.

Aspect angles are comma-separated degrees and/or the sets `major` and `octile`, read as a union,
so overlapping sets (both hold 90 and 180) can be combined. Time scales use the scripts' names.
Nothing is defaulted but the time scale: no aspect angles means none.
"""

from astro_research.astronomical_time import TimeScale
from astro_research.chart import ChartSettings, InvalidChartSettings
from astro_research.measurements import MAJOR, OCTILE

ASPECT_SETS = {"major": MAJOR, "octile": OCTILE}
TIME_SCALES = {
    "clock-as-ut1": TimeScale.CLOCK_AS_UT1,
    "ut1-from-delta-t": TimeScale.UT1_FROM_DELTA_T,
    "ut1-from-iers": TimeScale.UT1_FROM_IERS,
}
DEFAULT_TIME_SCALE = "ut1-from-iers"


class InvalidSettings(ValueError):
    """Text that doesn't name settings the engine can use."""


def chart_settings(aspects: str, time_scale: str = DEFAULT_TIME_SCALE) -> ChartSettings:
    if time_scale not in TIME_SCALES:
        raise InvalidSettings(
            f"time scale {time_scale!r} is not one of {', '.join(sorted(TIME_SCALES))}"
        )
    try:
        return ChartSettings(
            aspect_angles=aspect_angles(aspects), time_scale=TIME_SCALES[time_scale]
        )
    except InvalidChartSettings as error:
        raise InvalidSettings(str(error)) from error


def aspect_angles(text: str) -> tuple[float, ...]:
    angles: set[float] = set()
    for item in text.split(","):
        name = item.strip().lower()
        if not name:
            continue
        if name in ASPECT_SETS:
            angles.update(ASPECT_SETS[name])
            continue
        try:
            angles.add(float(name))
        except ValueError:
            raise InvalidSettings(f"aspects: {item!r} is neither an angle nor a set name") from None
    return tuple(sorted(angles))


def time_scale_name(scale: TimeScale) -> str:
    return next(name for name, member in TIME_SCALES.items() if member is scale)
