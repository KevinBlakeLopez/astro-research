"""Convert a group of the Open Gauquelin Database into files the platform's CSV import takes.

    python -m research_platform.open_gauquelin A1.csv --seed 1 --out DIR

The input is a group's CSV from opengauquelin.org, unzipped (comma-separated, with the columns
GQID, DATE, TZO, DATE-UT, LG and LAT among others). Three files are written to DIR:

- champions.csv: a row per person kept, in the import's columns, with group `champion`;
- controls.csv: a shuffled control per champion, with group `control`;
- left-out.csv: every row left out, by GQID, with its reason.

Each champion's time is DATE, the birth certificate's reading to the minute, with the offset
DATE minus DATE-UT to the second, so the import resolves it to the source's own universal time.
TZO, the source's stated offset, is given for only some rows and sometimes disagrees with
DATE-UT, so it's used only as a check. DATE 24:00 is midnight starting the next day. A row is
left out, with the first reason found, for: no birth time, no universal time, no coordinates, a
value that can't be read, an offset of more than 3 hours either way, or a TZO a minute or more
from the offset.

A control keeps a champion's date, offset and place, and takes another champion's clock time.
The champions are put in order by the SHA-256 of "<seed>:<GQID>", and each takes its clock time
from the next in that order (the last from the first), so no control takes its time from its
own champion, and every champion's time is used once. A control still has its champion's clock
time when the champion it takes it from was born at the same time, and then their charts are
the same. That's deliberate: in a shuffle, drawing a time equal to one's own is part of chance,
and refusing it would make the controls unlike the champions. No random-number generator is
involved, so the same seed gives the same controls on any Python version.

The import needs a category variable `group` with the categories champion and control, and a
source for each file: the database's group for the champions, and this converter, its commit and
the seed for the controls. The downloaded data and these files stay out of the repository.
"""

import argparse
import csv
import hashlib
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

NEEDED = ("GQID", "DATE", "TZO", "DATE-UT", "LG", "LAT")
IMPORT_COLUMNS = (
    "subject",
    "time",
    "time_kind",
    "utc_offset",
    "latitude",
    "longitude",
    "time_resolution",
    "group",
)
OUTPUTS = ("champions.csv", "controls.csv", "left-out.csv")
MOST = timedelta(hours=3)  # the largest offset kept, either way
AGREEMENT = timedelta(minutes=1)  # how close TZO must be to the offset: under the reading's minute
KINDS = (  # the kinds of reason, in the order they're checked
    "no birth time",
    "no universal time",
    "no coordinates",
    "unreadable",
    "more than 3 hours",
    "offset disagrees",
)
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_LOCAL = re.compile(r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})")
_UNIVERSAL = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?")
_OFFSET = re.compile(r"([+-])(\d{2}):(\d{2})(?::(\d{2}))?")


class SourceRefused(ValueError):
    """The file as a whole can't be read; the message says why."""


class _Refusal(Exception):
    """A row is left out; the message says why."""


@dataclass(frozen=True)
class Birth:
    label: str
    local: datetime  # the birth certificate's reading, with 24:00 as the next day's 00:00
    offset: timedelta  # local minus universal time
    latitude: str  # as written
    longitude: str


@dataclass(frozen=True)
class LeftOut:
    label: str
    reason: str


@dataclass(frozen=True)
class Conversion:
    champions: list[Birth]
    left_out: list[LeftOut]


def read(path: Path) -> list[dict[str, str]]:
    """The file's rows; raises SourceRefused if a column it needs is missing."""
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        missing = sorted(set(NEEDED) - set(reader.fieldnames or []))
        if missing:
            raise SourceRefused(f"the file has no {_either(missing)} column")
        return [dict(row) for row in reader]


def convert(rows: Sequence[dict[str, str]]) -> Conversion:
    """Every row kept as a champion or left out, in file order."""
    champions: list[Birth] = []
    left_out: list[LeftOut] = []
    for row in rows:
        try:
            champions.append(_champion(row))
        except _Refusal as refusal:
            left_out.append(LeftOut(label=row.get("GQID") or "", reason=str(refusal)))
    return Conversion(champions=champions, left_out=left_out)


def controls(champions: Sequence[Birth], seed: int) -> list[Birth]:
    """A control per champion, in the champions' order (see the module's description)."""
    if len(champions) < 2:
        raise ValueError("controls need at least two champions")
    order = sorted(range(len(champions)), key=lambda i: _digest(seed, champions[i].label))
    giver = {i: champions[order[(k + 1) % len(order)]] for k, i in enumerate(order)}
    width = max(4, len(str(len(champions))))
    return [
        Birth(
            label=f"control-{n:0{width}d}",
            local=datetime.combine(champion.local.date(), giver[i].local.time()),
            offset=champion.offset,
            latitude=champion.latitude,
            longitude=champion.longitude,
        )
        for n, (i, champion) in enumerate(enumerate(champions), start=1)
    ]


def import_rows(births: Sequence[Birth], group: str) -> list[dict[str, str]]:
    """The births as rows in the import's columns, each with `group`."""
    return [
        {
            "subject": birth.label,
            "time": birth.local.isoformat(timespec="minutes"),
            "time_kind": "offset",
            "utc_offset": offset_text(birth.offset),
            "latitude": birth.latitude,
            "longitude": birth.longitude,
            "time_resolution": "minute",
            "group": group,
        }
        for birth in births
    ]


def offset_text(offset: timedelta) -> str:
    """+HH:MM, or +HH:MM:SS when there are seconds; hours may run past 99."""
    sign = "-" if offset < timedelta(0) else "+"
    hours, rest = divmod(int(abs(offset).total_seconds()), 3600)
    minutes, seconds = divmod(rest, 60)
    text = f"{sign}{hours:02d}:{minutes:02d}"
    return f"{text}:{seconds:02d}" if seconds else text


def _champion(row: dict[str, str]) -> Birth:
    def cell(column: str) -> str:
        return row.get(column) or ""  # a short row's missing cells are None

    if not cell("DATE") or _DAY.fullmatch(cell("DATE")):
        raise _Refusal("no birth time")
    if not cell("DATE-UT"):
        raise _Refusal("no universal time")
    if not cell("LG") or not cell("LAT"):
        raise _Refusal("no coordinates")
    local = _local(cell("DATE"))
    universal = _universal(cell("DATE-UT"))
    stated = _stated(cell("TZO"))
    for column in ("LG", "LAT"):
        _coordinate(column, cell(column))
    offset = local - universal
    if abs(offset) > MOST:
        raise _Refusal(
            f"more than 3 hours: local time minus universal time is {offset_text(offset)}"
        )
    if stated is not None and abs(offset - stated) >= AGREEMENT:
        raise _Refusal(
            f"offset disagrees: TZO is {cell('TZO')}, local time minus universal time is "
            f"{offset_text(offset)}"
        )
    return Birth(
        label=cell("GQID"),
        local=local,
        offset=offset,
        latitude=cell("LAT"),
        longitude=cell("LG"),
    )


def _local(text: str) -> datetime:
    unreadable = _Refusal(f"unreadable: DATE {text!r} is not a date and time")
    found = _LOCAL.fullmatch(text)
    if found is None:
        raise unreadable
    day, clock = found.groups()
    try:
        if clock == "24:00":
            return datetime.combine(date.fromisoformat(day) + timedelta(days=1), time())
        return datetime.fromisoformat(f"{day}T{clock}")
    except ValueError:
        raise unreadable from None


def _universal(text: str) -> datetime:
    try:
        if _UNIVERSAL.fullmatch(text):
            return datetime.fromisoformat(text)
    except ValueError:
        pass
    raise _Refusal(f"unreadable: DATE-UT {text!r} is not a date and time")


def _stated(text: str) -> timedelta | None:
    if not text:
        return None
    found = _OFFSET.fullmatch(text)
    if found is None:
        raise _Refusal(f"unreadable: TZO {text!r} is not an offset")
    sign, hours, minutes, seconds = found.groups()
    offset = timedelta(hours=int(hours), minutes=int(minutes), seconds=int(seconds or 0))
    return -offset if sign == "-" else offset


def _coordinate(column: str, text: str) -> None:
    try:
        number = float(text)
    except ValueError:
        number = math.nan
    if not math.isfinite(number):
        raise _Refusal(f"unreadable: {column} {text!r} is not a number")


def _digest(seed: int, label: str) -> str:
    return hashlib.sha256(f"{seed}:{label}".encode()).hexdigest()


def _either(names: Sequence[str]) -> str:
    """A, B or C."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} or {names[-1]}"


def _rows(count: int) -> str:
    return "1 row" if count == 1 else f"{count} rows"


def _write(path: Path, columns: Sequence[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns)  # RFC 4180's \r\n
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert an Open Gauquelin group into champions and shuffled controls "
        "for the platform's CSV import."
    )
    parser.add_argument("file", type=Path, help="a group's CSV from opengauquelin.org, unzipped")
    parser.add_argument(
        "--seed", type=int, required=True, help="the seed that orders the controls' clock times"
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="the folder to write champions.csv, controls.csv and left-out.csv to",
    )
    args = parser.parse_args(argv)
    out: Path = args.out
    try:
        conversion = convert(read(args.file))
        made = controls(conversion.champions, args.seed)
    except ValueError as error:  # SourceRefused, or too few champions
        raise SystemExit(f"nothing was written: {error}") from None
    for name in OUTPUTS:
        if (out / name).exists():
            raise SystemExit(
                f"nothing was written: {out / name} already exists; choose another --out folder"
            )
    out.mkdir(parents=True, exist_ok=True)
    _write(out / "champions.csv", IMPORT_COLUMNS, import_rows(conversion.champions, "champion"))
    _write(out / "controls.csv", IMPORT_COLUMNS, import_rows(made, "control"))
    _write(
        out / "left-out.csv",
        ("GQID", "reason"),
        [{"GQID": gone.label, "reason": gone.reason} for gone in conversion.left_out],
    )
    kinds = Counter(gone.reason.split(":")[0] for gone in conversion.left_out)
    counted = ", ".join(f"{kind} {kinds[kind]}" for kind in KINDS if kinds[kind])
    print(f"champions.csv: {_rows(len(conversion.champions))}")
    print(f"controls.csv: {_rows(len(made))}, seed {args.seed}")
    print(f"left-out.csv: {_rows(len(conversion.left_out))}" + (f" ({counted})" if counted else ""))
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
