"""Astronomical time for a resolved civil instant.

Swiss Ephemeris takes Julian dates: UT1 for the Earth's rotation (angles, houses, rise and set)
and TT for planetary positions. How a civil UTC reading becomes UT1 is a convention, and the
default between the two below is deliberately not chosen yet, so every caller names one.

Before 1972 civil time was based on UT itself, and both conventions give identical results.
After that they differ by UT1 - UTC, at most 0.9 s. That moves the Ascendant by up to about
30 arcsec, roughly a third of what a one-second birth-time difference does.

Values are computed by `Ephemeris.time()` in the adapter; this module only defines them.
"""

import enum
from dataclasses import dataclass

from astro_research.civil_time import ResolvedInstant


class InstantInLeapSecond(ValueError):
    """The instant falls inside a leap second, which a UTC reading (a datetime) cannot express."""


class TimeScale(enum.Enum):
    CLOCK_AS_UT1 = "clock_as_ut1"
    """The UTC clock reading is used as UT1, as most astrology software does."""

    UT1_FROM_DELTA_T = "ut1_from_delta_t"
    """UTC -> TT exactly via leap seconds; UT1 = TT - delta-T (Swiss Ephemeris's model)."""


@dataclass(frozen=True, kw_only=True)
class AstronomicalTime:
    instant: ResolvedInstant  # the original input travels with every result
    time_scale: TimeScale
    jd_ut1: float  # Julian date, UT1
    jd_tt: float  # Julian date, TT, exactly as Swiss Ephemeris applies it to positions
    delta_t_s: float  # TT - UT1 in seconds; depends on the loaded DE before 1955
    ut1_minus_utc_s: float  # 0 under CLOCK_AS_UT1, and before 1972 under either convention
