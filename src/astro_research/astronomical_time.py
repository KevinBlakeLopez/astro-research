"""Astronomical time for a resolved civil instant.

Swiss Ephemeris takes Julian dates: UT1 for the Earth's rotation (angles, houses, rise and set)
and TT for planetary positions. How a civil UTC reading becomes UT1 is a convention. The
default, `UT1_FROM_IERS`, takes IERS's measured UT1 - UTC; the other two are kept for
comparison and for dates IERS's packaged table doesn't cover.

Before 1972 civil time was based on UT itself, and all three scales take the reading as UT.
After that they differ by up to 0.9 s, the size of UT1 - UTC. That moves the Ascendant by up to
about 30 arcsec, roughly a third of what a one-second birth-time difference does. Swiss
Ephemeris's delta-T model agrees with IERS within 0.066 s for 1972-2022, then drifts to 0.40 s
by 2026 (measured).

Values are computed by `Ephemeris.time()` in the adapter; this module only defines them.
"""

import enum
from dataclasses import dataclass

from astro_research.civil_time import ResolvedInstant


class InstantInLeapSecond(ValueError):
    """The instant falls inside a leap second, which a UTC reading (a datetime) cannot express."""


class TimeScale(enum.Enum):
    CLOCK_AS_UT1 = "clock_as_ut1"
    """The UTC clock reading is used as UT1 (Immanuel does this, observed)."""

    UT1_FROM_DELTA_T = "ut1_from_delta_t"
    """UTC -> TT exactly via leap seconds; UT1 = TT - delta-T (Swiss Ephemeris's model)."""

    UT1_FROM_IERS = "ut1_from_iers"
    """UTC -> TT exactly via leap seconds; UT1 = TT - delta-T, with delta-T from IERS's daily
    UT1 - UTC, interpolated linearly in TT. Refused after the packaged table's last day."""


class Ut1Basis(enum.Enum):
    """Where one instant's UT1 came from."""

    CLOCK_READING = "clock_reading"
    """The UTC reading itself, by the clock convention."""

    CIVIL_TIME_AS_UT = "civil_time_as_ut"
    """Before 1972, when civil time was UT: the reading itself."""

    DELTA_T_MODEL = "delta_t_model"
    """TT less Swiss Ephemeris's delta-T model."""

    IERS_C04 = "iers_c04"
    """TT less delta-T from IERS's EOP 20 C04 values."""


@dataclass(frozen=True, kw_only=True)
class AstronomicalTime:
    instant: ResolvedInstant  # the original input travels with every result
    time_scale: TimeScale
    jd_ut1: float  # Julian date, UT1
    jd_tt: float  # Julian date, TT, exactly as Swiss Ephemeris applies it to positions
    delta_t_s: float  # TT - UT1 in seconds; depends on the loaded DE before 1955
    ut1_minus_utc_s: float  # 0 under CLOCK_AS_UT1, and before 1972 under every scale
    ut1_basis: Ut1Basis
