"""AstroChart Bridge's portable charts (`.astrochart.json`, version 1), read as its own reader
reads them.

The format is AstroChart Bridge's (`docs/portable-charts.md` in its repository), and its
reference reader is `parse()` in `extension/core/portable.js`. This one refuses what that one
refuses, with the same reason, and reads what it reads: `tests/fixtures/astrochart/` holds
inputs and what AstroChart Bridge made of each. Where Python and JavaScript differ, JavaScript's
behaviour is the format's:

- numbers are doubles, as JSON.parse reads them, so `1.0` is version 1, `true` is never a
  number, and `NaN` or `Infinity` isn't JSON at all;
- lengths are UTF-16 code units, as a JavaScript string counts them, so an emoji is two;
- a date is matched in ASCII digits only, with nothing after it, and a year from 0 to 99 is
  never a valid date: JavaScript's Date.UTC reads it as 1900 to 1999;
- a name made only of whitespace is refused, with JavaScript's trim() deciding what's
  whitespace.

Control and format characters are found with Python's own Unicode tables, which may be a
version older than JavaScript's.

The chart's `local` time and `utcOffsetSeconds` are what the source stated: a wall-clock
reading and its offset to the second. `source` says where AstroChart Bridge read it from, and
`labels` hold the chart's name and town when its sender included them. Unknown fields in either
are dropped; an unknown field anywhere else refuses the chart.
"""

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, TypeGuard

FORMAT = "astrochart"
VERSION = 1
MAX_TEXT_LENGTH = 16_384  # UTF-16 code units
FIRST_GREGORIAN = "1582-10-15T00:00:00"
MIN_OFFSET_SECONDS = -12 * 3600
MAX_OFFSET_SECONDS = 14 * 3600
TOP_KEYS = frozenset({"format", "version", "chart", "source", "labels"})
CHART_KEYS = frozenset({"calendar", "local", "utcOffsetSeconds", "latitude", "longitude"})
MAX_LABEL_LENGTH = 100

_LOCAL = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})")
_CODE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# What JavaScript's String.prototype.trim() removes: WhiteSpace and LineTerminator.
_JS_WHITESPACE = "".join(
    chr(code)
    for code in (
        *(0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0xA0, 0x1680),
        *range(0x2000, 0x200B),
        *(0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF),
    )
)
_JOINERS = frozenset({chr(0x200C), chr(0x200D)})  # some scripts' names are spelled with them


class RefusedChart(ValueError):
    """Text AstroChart Bridge's reader would refuse; `reason` is its reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class PortableChart:
    local: str  # the wall-clock time at the birthplace, YYYY-MM-DDTHH:MM:SS
    utc_offset_seconds: int  # east of UTC, as the source stated it
    latitude: float
    longitude: float
    source: dict[str, str]  # site, via, zoneLabel, utcBasis: those given
    labels: dict[str, str]  # name, town: those given


def read(text: str) -> PortableChart:
    """The chart in a file's text, or `RefusedChart` with AstroChart Bridge's reason."""
    if _units(text) > MAX_TEXT_LENGTH:
        raise RefusedChart("too-large")
    try:
        document = json.loads(text, parse_int=float, parse_constant=_not_json)
    except (ValueError, RecursionError):
        raise RefusedChart("malformed") from None
    return _check(document)


def _check(document: Any) -> PortableChart:
    """The checks of AstroChart Bridge's check(), in its order: the first one failed is the
    reason."""
    if not isinstance(document, dict):
        raise RefusedChart("malformed")
    if document.get("format") != FORMAT:
        raise RefusedChart("not-astrochart")
    if not _is_integer(document.get("version")):
        raise RefusedChart("malformed")
    if document["version"] != VERSION:
        raise RefusedChart("unsupported-version")
    if not set(document) <= TOP_KEYS:
        raise RefusedChart("unknown-field")

    chart = document.get("chart")
    if not isinstance(chart, dict):
        raise RefusedChart("malformed")
    if not set(chart) <= CHART_KEYS:
        raise RefusedChart("unknown-field")
    if chart.get("calendar") != "gregorian":
        raise RefusedChart("unsupported-calendar")
    local = chart.get("local")
    if not _is_local_time(local):
        raise RefusedChart("invalid-date")
    assert isinstance(local, str)
    if local < FIRST_GREGORIAN:  # the fixed-width form sorts like the dates it writes
        raise RefusedChart("before-gregorian")
    offset = chart.get("utcOffsetSeconds")
    if not _is_integer(offset) or not MIN_OFFSET_SECONDS <= offset <= MAX_OFFSET_SECONDS:
        raise RefusedChart("invalid-offset")
    try:  # a UTC past year 9999 has no YYYY form
        datetime.fromisoformat(local) - timedelta(seconds=offset)
    except OverflowError:
        raise RefusedChart("invalid-date") from None
    latitude, longitude = chart.get("latitude"), chart.get("longitude")
    if not _is_degrees(latitude, 90) or not _is_degrees(longitude, 180):
        raise RefusedChart("invalid-coordinates")

    source = _known(
        document,
        "source",
        {"site": _is_code, "via": _is_code, "zoneLabel": _is_zone_label, "utcBasis": _is_code},
        "invalid-source",
    )
    labels = _known(
        document, "labels", {"name": _is_label_text, "town": _is_label_text}, "invalid-labels"
    )
    return PortableChart(
        local=local,
        utc_offset_seconds=int(offset),
        latitude=latitude + 0.0,  # -0 becomes 0, as AstroChart Bridge writes it
        longitude=longitude + 0.0,
        source=source,
        labels=labels,
    )


def _known(
    document: dict[str, Any], key: str, fields: dict[str, Any], reason: str
) -> dict[str, str]:
    """The section's known fields, in the format's order, each checked; others are dropped."""
    section = document.get(key, {})
    if not isinstance(section, dict):
        raise RefusedChart(reason)
    for field, valid in fields.items():
        if field in section and not valid(section[field]):
            raise RefusedChart(reason)
    return {field: section[field] for field in fields if field in section}


def _not_json(constant: str) -> None:
    raise ValueError(f"{constant} isn't JSON")


def _units(text: str) -> int:
    """The length JavaScript gives a string: UTF-16 code units."""
    return sum(2 if ord(character) > 0xFFFF else 1 for character in text)


def _is_number(value: Any) -> TypeGuard[float]:
    return isinstance(value, float)  # every JSON number is read as one


def _is_integer(value: Any) -> TypeGuard[float]:
    """JavaScript's Number.isInteger."""
    return _is_number(value) and math.isfinite(value) and value.is_integer()


def _is_degrees(value: Any, limit: float) -> TypeGuard[float]:
    return _is_number(value) and abs(value) <= limit  # infinity is past every limit


def _is_local_time(value: Any) -> bool:
    match = _LOCAL.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        return False
    year, month, day, hour, minute, second = (int(part) for part in match.groups())
    if year <= 99:  # Date.UTC reads it as 1900 + year, so it never matches
        return False
    try:
        date(year, month, day)
    except ValueError:
        return False
    return hour <= 23 and minute <= 59 and second <= 59


def _is_well_formed(text: str) -> bool:
    """No lone surrogate, as String.prototype.isWellFormed checks."""
    return not any(0xD800 <= ord(character) <= 0xDFFF for character in text)


def _is_code(value: Any) -> bool:
    """A code such as "astro-seek": lower-case words joined by hyphens."""
    return isinstance(value, str) and len(value) <= 32 and _CODE.fullmatch(value) is not None


def _is_zone_label(value: Any) -> bool:
    """A zone label as the page printed it, with no control or format characters."""
    return (
        isinstance(value, str)
        and 1 <= _units(value) <= 64
        and _is_well_formed(value)
        and not any(unicodedata.category(c) in ("Cc", "Cf") for c in value)
    )


def _is_label_text(value: Any) -> bool:
    """A name or town: control characters and format characters are refused, except the
    zero-width joiner and non-joiner."""
    return (
        isinstance(value, str)
        and len(value.strip(_JS_WHITESPACE)) >= 1
        and _units(value) <= MAX_LABEL_LENGTH
        and _is_well_formed(value)
        and not any(
            unicodedata.category(c) == "Cc"
            or (unicodedata.category(c) == "Cf" and c not in _JOINERS)
            for c in value
        )
    )
