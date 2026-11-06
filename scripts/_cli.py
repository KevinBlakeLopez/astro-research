"""Shared by the example scripts: read a birth record and settings from options, print tables.

The scripts are demonstrations of the engine, not its interface: plain text for people, no
machine-readable output (how to serialise results is a later decision).
"""

import argparse
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from astro_research.angles import AnglesUndefined
from astro_research.astronomical_time import TimeScale
from astro_research.chart import (
    BirthRecord,
    ChartSettings,
    InvalidBirthRecord,
    InvalidChartSettings,
)
from astro_research.civil_time import (
    CivilTime,
    CivilTimeError,
    LocalMeanTime,
    OffsetLocalTime,
    UtcTime,
    ZonedLocalTime,
)
from astro_research.comparison import ChartsNotComparable
from astro_research.ephemeris import EphemerisEnvironmentError
from astro_research.geo import GeoPosition, InvalidCoordinates
from astro_research.measurements import MAJOR, OCTILE

DEFAULT_EPHE = Path(__file__).resolve().parents[1] / "ephe"
ASPECT_SETS = {"major": MAJOR, "octile": OCTILE}
TIME_SCALES = {
    "clock-as-ut1": TimeScale.CLOCK_AS_UT1,
    "ut1-from-delta-t": TimeScale.UT1_FROM_DELTA_T,
}
# Every refusal the engine can raise for a record or settings; printed, never guessed around.
REFUSALS = (
    CivilTimeError,
    InvalidBirthRecord,
    InvalidChartSettings,
    InvalidCoordinates,
    AnglesUndefined,
    ChartsNotComparable,
    EphemerisEnvironmentError,
)
_OFFSET = re.compile(r"([+-])(\d{1,2}):(\d{2})(?::(\d{2}))?")


def add_record_options(parser: argparse.ArgumentParser, prefix: str = "") -> None:
    """Options for one birth record; `prefix` is e.g. "a-" for --a-local, --a-zone, ..."""
    p = f"--{prefix}"
    group = parser.add_argument_group(f"record {prefix.rstrip('-').upper()}".strip())
    when = group.add_mutually_exclusive_group(required=True)
    when.add_argument(f"{p}utc", metavar="ISO", help="a UTC instant, e.g. 1987-06-15T03:25:00")
    when.add_argument(
        f"{p}local",
        metavar="ISO",
        help="a wall-clock reading, e.g. 1987-06-15T04:25, with --zone, --offset or "
        "--local-mean-time",
    )
    group.add_argument(f"{p}zone", metavar="IANA", help="the zone for --local, e.g. Europe/London")
    group.add_argument(
        f"{p}offset",
        metavar="+HH:MM",
        help="the UTC offset the source states for --local, e.g. --offset=-05:00",
    )
    group.add_argument(
        f"{p}local-mean-time", action="store_true", help="--local is local mean time at the place"
    )
    group.add_argument(
        f"{p}stated-offset",
        metavar="+HH:MM",
        help="the offset that settles an ambiguous --zone time, e.g. --stated-offset=+01:00",
    )
    group.add_argument(
        f"{p}accept-pre-1970-tzdb", action="store_true", help="accept tzdb rules before 1970"
    )
    group.add_argument(f"{p}lat", type=float, required=True, help="latitude, degrees north")
    group.add_argument(f"{p}lon", type=float, required=True, help="longitude, degrees east")
    group.add_argument(f"{p}elevation", type=float, default=0.0, help="metres (default 0)")


def add_settings_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--time-scale", choices=sorted(TIME_SCALES), required=True, help="how UTC becomes UT1"
    )
    parser.add_argument(
        "--aspects",
        required=True,
        help="comma-separated aspect angles in degrees and/or the sets "
        f"{', '.join(sorted(ASPECT_SETS))}, e.g. major,octile",
    )
    parser.add_argument("--ephe", type=Path, default=DEFAULT_EPHE, help="the ephemeris folder")


def record_from(
    parser: argparse.ArgumentParser, args: argparse.Namespace, prefix: str = ""
) -> BirthRecord:
    def get(name: str) -> object:
        return getattr(args, (prefix + name).replace("-", "_"))

    p = f"--{prefix}"
    time: CivilTime
    if get("utc") is not None:
        moment = datetime.fromisoformat(str(get("utc")))
        time = UtcTime(utc=moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC))
    else:
        local = datetime.fromisoformat(str(get("local")))
        sources = [get("zone") is not None, get("offset") is not None, bool(get("local-mean-time"))]
        if sources.count(True) != 1:
            parser.error(f"{p}local needs exactly one of {p}zone, {p}offset, {p}local-mean-time")
        if get("zone") is not None:
            stated = get("stated-offset")
            time = ZonedLocalTime(
                local=local,
                zone=str(get("zone")),
                stated_utc_offset=None if stated is None else _offset(parser, str(stated)),
                accept_pre_1970_tzdb=bool(get("accept-pre-1970-tzdb")),
            )
        elif get("offset") is not None:
            time = OffsetLocalTime(local=local, utc_offset=_offset(parser, str(get("offset"))))
        else:
            time = LocalMeanTime(local=local)
    place = GeoPosition(
        latitude=float(str(get("lat"))),
        longitude=float(str(get("lon"))),
        elevation_m=float(str(get("elevation"))),
    )
    return BirthRecord(time=time, place=place)


def settings_from(parser: argparse.ArgumentParser, args: argparse.Namespace) -> ChartSettings:
    angles: set[float] = set()
    for item in str(args.aspects).split(","):
        name = item.strip().lower()
        if name in ASPECT_SETS:
            angles.update(ASPECT_SETS[name])
        else:
            try:
                angles.add(float(name))
            except ValueError:
                parser.error(f"--aspects: {item!r} is neither an angle nor a set name")
    return ChartSettings(
        time_scale=TIME_SCALES[args.time_scale], aspect_angles=tuple(sorted(angles))
    )


def _offset(parser: argparse.ArgumentParser, text: str) -> timedelta:
    match = _OFFSET.fullmatch(text.strip())
    if match is None:
        parser.error(f"offset {text!r} is not of the form +HH:MM")
    sign, hours, minutes, seconds = match.groups()
    size = timedelta(hours=int(hours), minutes=int(minutes), seconds=int(seconds or 0))
    return -size if sign == "-" else size


def offset_text(offset: timedelta) -> str:
    """+HH:MM, with seconds and microseconds when the offset has them (local mean time)."""
    total = offset // timedelta(microseconds=1)
    sign, total = ("-", -total) if total < 0 else ("+", total)
    seconds, micro = divmod(total, 1_000_000)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    text = f"{sign}{hours:02d}:{minutes:02d}"
    if seconds or micro:
        text += f":{seconds:02d}" + (f".{micro:06d}" if micro else "")
    return text


def utc_text(moment: datetime) -> str:
    fraction = f".{moment.microsecond:06d}" if moment.microsecond else ""
    return f"{moment:%Y-%m-%d %H:%M:%S}{fraction} UTC"


def table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Columns of numbers right-aligned, columns of names left-aligned."""
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    numeric = [all(_is_number(r[i]) for r in rows if r[i]) for i in range(len(headers))]

    def line(cells: Sequence[str]) -> str:
        return "  ".join(
            cell.rjust(width) if right else cell.ljust(width)
            for cell, width, right in zip(cells, widths, numeric, strict=True)
        ).rstrip()

    return "\n".join([line(headers), *(line(row) for row in rows)])


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def degrees(value: float) -> str:
    return f"{value:.8f}"


def arcsec(value_degrees: float) -> str:
    return f"{value_degrees * 3600:.4f}"
