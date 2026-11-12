"""Converting the Open Gauquelin Database's group A1 into files the CSV import takes.

`research_platform.open_gauquelin` reads a group's CSV (comma-separated, with the columns GQID,
DATE, TZO, DATE-UT, LG and LAT among others) and writes three files:

- champions.csv: a row per person kept, in the import's columns, with group `champion`;
- controls.csv: a shuffled control per champion, with group `control`;
- left-out.csv: every row left out, by GQID, with its reason.

Each champion's time is DATE, the birth certificate's reading, with the offset DATE minus
DATE-UT to the second, so the import resolves it to the source's own universal time. TZO, the
source's stated offset, is given for only some rows and sometimes disagrees with DATE-UT; it's
used only as a check. DATE 24:00 is midnight starting the next day. A row is left out, with the
first reason found, for: no birth time, no universal time, no coordinates, a value that can't be
read, an offset of more than 3 hours either way, or a TZO a minute or more from the offset.

A control keeps a champion's date, offset and place and takes another champion's clock time.
The champions are put in order by the SHA-256 of "<seed>:<GQID>", and each takes its clock time
from the next in that order (the last from the first), so no control takes its time from its
own champion, and every champion's time is used once. A control still has its champion's clock
time when the champion it takes it from was born at the same time; that isn't avoided. The same
seed gives the same controls on any Python version, since no random-number generator is
involved.

`read_text` reads a file's text as `read` reads the file, and `csv_text` gives the text the
command writes to a file, for the desktop app's demo (test_demo.py), which has no files.

Every person here is made up.
"""

import csv
import hashlib
import io
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from astro_research.civil_time import resolve
from research_platform import open_gauquelin as og
from research_platform.csv_import import plan_import, read_text
from research_platform.models import Category, Source, Variable

HEADER = [
    "GQID",
    "MUID",
    "NUM",
    "FNAME",
    "GNAME",
    "DATE",
    "TZO",
    "DATE-UT",
    "PLACE",
    "C2",
    "C3",
    "CY",
    "LG",
    "LAT",
    "GEOID",
    "OCCU",
]
IMPORT_COLUMNS = [
    "subject",
    "time",
    "time_kind",
    "utc_offset",
    "latitude",
    "longitude",
    "time_resolution",
    "group",
]


def person(n: int, **cells: str) -> dict[str, str]:
    """Made-up person n, born at n o'clock and 15 minutes on 1930-01-n, 1 hour and n minutes
    ahead of UT (so no two have the same offset); `cells` replaces any of the columns."""
    local = datetime(1930, 1, n, n % 24, 15)
    offset = timedelta(hours=1, minutes=n)
    row = {
        "GQID": f"A1-{n}",
        "MUID": "",
        "NUM": str(n),
        "FNAME": "Synthetic",
        "GNAME": f"Person {n}",
        "DATE": local.strftime("%Y-%m-%d %H:%M"),
        "TZO": f"+01:{n:02d}",
        "DATE-UT": (local - offset).strftime("%Y-%m-%d %H:%M"),
        "PLACE": "Testville",
        "C2": "",
        "C3": "",
        "CY": "FR",
        "LG": f"{n / 4:g}",
        "LAT": f"{44 + n / 10:g}",
        "GEOID": "",
        "OCCU": "cyclist",
    }
    return row | cells


def source_file(directory: Path, rows: list[dict[str, str]], header: list[str] = HEADER) -> Path:
    path = directory / "A1.csv"
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=header, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def converted(tmp_path: Path, rows: list[dict[str, str]]) -> og.Conversion:
    return og.convert(og.read(source_file(tmp_path, rows)))


def one(tmp_path: Path, **cells: str) -> dict[str, str]:
    """The import row of a single made-up person with `cells`."""
    conversion = converted(tmp_path, [person(1, **cells)])
    assert conversion.left_out == [], conversion.left_out
    (row,) = og.import_rows(conversion.champions, "champion")
    return row


def reason(tmp_path: Path, **cells: str) -> str:
    """Why a single made-up person with `cells` is left out."""
    conversion = converted(tmp_path, [person(1, **cells)])
    assert conversion.champions == []
    (left_out,) = conversion.left_out
    assert left_out.label == "A1-1"
    return left_out.reason


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


# --- Champions ------------------------------------------------------------------------------


def test_a_row_becomes_the_import_s_columns(tmp_path: Path) -> None:
    row = one(tmp_path, DATE="1931-05-04 07:15", TZO="+00:00", **{"DATE-UT": "1931-05-04 07:15"})
    assert list(row) == IMPORT_COLUMNS
    assert row == {
        "subject": "A1-1",
        "time": "1931-05-04T07:15",
        "time_kind": "offset",
        "utc_offset": "+00:00",
        "latitude": "44.1",
        "longitude": "0.25",
        "time_resolution": "minute",
        "group": "champion",
    }


@pytest.mark.parametrize(
    ("local", "stated", "universal", "offset"),
    [
        ("1890-03-02 10:00", "+00:09:21", "1890-03-02 09:50:39", "+00:09:21"),
        ("1900-01-01 12:00", "+00:09:21", "1900-01-01 11:50:40", "+00:09:20"),  # UT, not TZO
        ("1885-06-01 08:00", "", "1885-06-01 07:10:04", "+00:49:56"),  # no TZO
        ("1880-02-02 06:00", "-00:13:29", "1880-02-02 06:13:29", "-00:13:29"),  # west
        ("1925-07-07 22:00", "", "1925-07-07 20:00", "+02:00"),
        ("1925-07-07 00:30", "", "1925-07-06 23:30", "+01:00"),  # UT on the day before
    ],
)
def test_the_offset_is_local_time_minus_universal_time_to_the_second(
    tmp_path: Path, local: str, stated: str, universal: str, offset: str
) -> None:
    row = one(tmp_path, DATE=local, TZO=stated, **{"DATE-UT": universal})
    assert row["time"] == local.replace(" ", "T")
    assert row["utc_offset"] == offset


def test_24_00_is_midnight_starting_the_next_day(tmp_path: Path) -> None:
    row = one(tmp_path, DATE="1932-08-06 24:00", TZO="", **{"DATE-UT": "1932-08-06 23:00"})
    assert (row["time"], row["utc_offset"]) == ("1932-08-07T00:00", "+01:00")
    # and checked like any other time
    late = reason(tmp_path, DATE="1931-04-12 24:00", TZO="", **{"DATE-UT": "1931-04-12 00:00"})
    assert late == "more than 3 hours: local time minus universal time is +24:00"


# --- Rows left out --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cells", "why"),
    [
        ({"DATE": "1926-11-26"}, "no birth time"),
        ({"DATE": ""}, "no birth time"),
        ({"DATE-UT": ""}, "no universal time"),
        ({"LG": ""}, "no coordinates"),
        ({"LAT": ""}, "no coordinates"),
        (
            {"DATE": "1926-13-40 10:00"},
            "unreadable: DATE '1926-13-40 10:00' is not a date and time",
        ),
        (
            {"DATE": "1926-11-26 10:00:30"},  # DATE is to the minute
            "unreadable: DATE '1926-11-26 10:00:30' is not a date and time",
        ),
        ({"DATE-UT": "soon"}, "unreadable: DATE-UT 'soon' is not a date and time"),
        ({"DATE-UT": "1930-01-01"}, "unreadable: DATE-UT '1930-01-01' is not a date and time"),
        ({"TZO": "+1"}, "unreadable: TZO '+1' is not an offset"),
        ({"LG": "east"}, "unreadable: LG 'east' is not a number"),
        ({"LAT": "nan"}, "unreadable: LAT 'nan' is not a number"),
        (
            {"DATE": "1929-01-20 16:00", "TZO": "+00:00", "DATE-UT": "1929-01-20 08:00"},
            "more than 3 hours: local time minus universal time is +08:00",
        ),
        (
            {"DATE": "1923-12-08 00:00", "TZO": "+00:00", "DATE-UT": "1923-12-08 05:00"},
            "more than 3 hours: local time minus universal time is -05:00",
        ),
        (
            {"DATE": "1892-10-08 09:10", "TZO": "", "DATE-UT": "1891-10-08 08:20:04"},
            "more than 3 hours: local time minus universal time is +8784:49:56",
        ),
        (
            {"DATE": "1920-01-01 12:00", "TZO": "+01:00", "DATE-UT": "1920-01-01 12:00"},
            "offset disagrees: TZO is +01:00, local time minus universal time is +00:00",
        ),
        (
            {"DATE": "1890-01-01 12:00", "TZO": "+00:06:39", "DATE-UT": "1890-01-01 11:50:48"},
            "offset disagrees: TZO is +00:06:39, local time minus universal time is +00:09:12",
        ),
    ],
)
def test_a_row_left_out_says_why(tmp_path: Path, cells: dict[str, str], why: str) -> None:
    assert reason(tmp_path, **cells) == why


def test_the_first_reason_found_is_given(tmp_path: Path) -> None:
    assert reason(tmp_path, DATE="1926-11-26", LG="", **{"DATE-UT": ""}) == "no birth time"
    assert reason(tmp_path, LG="", **{"DATE-UT": ""}) == "no universal time"
    assert reason(tmp_path, LG="", TZO="+1") == "no coordinates"


@pytest.mark.parametrize(
    ("universal", "kept"),
    [
        ("1930-01-01 09:00", True),  # exactly 3 hours
        ("1930-01-01 08:59:59", False),
        ("1930-01-01 15:00", True),  # exactly -3 hours
        ("1930-01-01 15:00:01", False),
    ],
)
def test_an_offset_of_3_hours_either_way_is_kept(
    tmp_path: Path, universal: str, kept: bool
) -> None:
    rows = [person(1, DATE="1930-01-01 12:00", TZO="", **{"DATE-UT": universal})]
    assert bool(converted(tmp_path, rows).champions) is kept


@pytest.mark.parametrize(
    ("universal", "kept"),
    [
        ("1930-01-01 10:59:01", True),  # 59 seconds from TZO
        ("1930-01-01 10:59", False),  # 60 seconds
    ],
)
def test_a_tzo_under_a_minute_from_the_offset_is_kept(
    tmp_path: Path, universal: str, kept: bool
) -> None:
    rows = [person(1, DATE="1930-01-01 12:00", TZO="+01:00", **{"DATE-UT": universal})]
    assert bool(converted(tmp_path, rows).champions) is kept


def test_every_row_is_kept_or_left_out_in_file_order(tmp_path: Path) -> None:
    rows = [
        person(1),
        person(2, DATE="1930-01-02"),
        person(3),
        person(4, **{"DATE-UT": ""}),
        person(5),
    ]
    conversion = converted(tmp_path, rows)
    assert [birth.label for birth in conversion.champions] == ["A1-1", "A1-3", "A1-5"]
    assert [(gone.label, gone.reason) for gone in conversion.left_out] == [
        ("A1-2", "no birth time"),
        ("A1-4", "no universal time"),
    ]


def test_a_file_without_a_column_it_needs_is_refused(tmp_path: Path) -> None:
    header = [column for column in HEADER if column not in ("TZO", "DATE-UT")]
    path = source_file(tmp_path, [person(1)], header)
    with pytest.raises(og.SourceRefused, match="the file has no DATE-UT or TZO column"):
        og.read(path)


def test_a_byte_order_mark_is_accepted(tmp_path: Path) -> None:
    path = source_file(tmp_path, [person(1)])
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert [birth.label for birth in og.convert(og.read(path)).champions] == ["A1-1"]


def test_a_file_s_text_reads_as_the_file_does(tmp_path: Path) -> None:
    path = source_file(tmp_path, [person(1), person(2)])
    assert og.read_text(path.read_text(encoding="utf-8")) == og.read(path)


def test_a_text_without_a_column_it_needs_is_refused() -> None:
    header = ",".join(column for column in HEADER if column not in ("TZO", "DATE-UT"))
    with pytest.raises(og.SourceRefused, match="the file has no DATE-UT or TZO column"):
        og.read_text(header + "\n")


# --- Controls -------------------------------------------------------------------------------


def champions(tmp_path: Path, count: int) -> list[og.Birth]:
    """`count` made-up champions, each born at a different clock time."""
    conversion = converted(tmp_path, [person(n) for n in range(1, count + 1)])
    assert len(conversion.champions) == count
    return conversion.champions


def test_a_control_keeps_its_champion_s_date_offset_and_place(tmp_path: Path) -> None:
    kept = champions(tmp_path, 10)
    for champion, control in zip(kept, og.controls(kept, seed=1), strict=True):
        assert control.local.date() == champion.local.date()
        assert control.offset == champion.offset
        assert (control.latitude, control.longitude) == (champion.latitude, champion.longitude)


def test_a_control_takes_another_champion_s_clock_time_and_each_is_used_once(
    tmp_path: Path,
) -> None:
    kept = champions(tmp_path, 10)
    made = og.controls(kept, seed=1)
    for champion, control in zip(kept, made, strict=True):
        assert control.local.time() != champion.local.time()
    assert Counter(c.local.time() for c in made) == Counter(c.local.time() for c in kept)


def givers(kept: list[og.Birth], seed: int) -> dict[str, og.Birth]:
    """Whose clock time each champion's control takes, by the rule: the next champion in the
    order of SHA-256 of "<seed>:<GQID>", the last taking the first's."""

    def digest(birth: og.Birth) -> str:
        return hashlib.sha256(f"{seed}:{birth.label}".encode()).hexdigest()

    order = sorted(kept, key=digest)
    return {birth.label: order[(k + 1) % len(order)] for k, birth in enumerate(order)}


def test_the_clock_times_follow_the_seed_s_order(tmp_path: Path) -> None:
    kept = champions(tmp_path, 10)
    giver = givers(kept, seed=7)
    made = og.controls(kept, seed=7)
    assert [c.local.time() for c in made] == [giver[c.label].local.time() for c in kept]


def test_a_control_can_have_its_champion_s_clock_time(tmp_path: Path) -> None:
    rows = [
        person(
            n,
            DATE=f"1930-01-{n:02d} {hour:02d}:00",
            TZO="",
            **{"DATE-UT": f"1930-01-{n:02d} {hour - 1:02d}:00"},
        )
        for n, hour in zip(range(1, 11), [12, 8] * 5, strict=True)
    ]  # born at noon or at 8, as most real times are on the hour
    kept = converted(tmp_path, rows).champions
    giver = givers(kept, seed=7)
    made = og.controls(kept, seed=7)
    assert [c.local.time() for c in made] == [giver[c.label].local.time() for c in kept]
    same = [c.label for c, k in zip(made, kept, strict=True) if c == replace(k, label=c.label)]
    assert same  # some controls are their champions under another label: that's not avoided


def test_the_same_seed_gives_the_same_controls_and_another_seed_others(tmp_path: Path) -> None:
    kept = champions(tmp_path, 10)
    assert og.controls(kept, seed=1) == og.controls(kept, seed=1)
    assert og.controls(kept, seed=1) != og.controls(kept, seed=2)


def test_controls_are_numbered_in_their_champions_order(tmp_path: Path) -> None:
    kept = champions(tmp_path, 3)
    rows = og.import_rows(og.controls(kept, seed=1), "control")
    assert [row["subject"] for row in rows] == ["control-0001", "control-0002", "control-0003"]
    assert {row["group"] for row in rows} == {"control"}


def test_controls_need_two_champions(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="controls need at least two champions"):
        og.controls(champions(tmp_path, 1), seed=1)


# --- The command ----------------------------------------------------------------------------


def test_the_command_writes_the_three_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [
        person(1),
        person(2, DATE="1930-01-02"),
        person(3),
        person(4, LG=""),
        person(5),
        person(6, DATE="1930-01-06 16:00", TZO="", **{"DATE-UT": "1930-01-06 08:00"}),
    ]
    out = tmp_path / "new" / "converted"  # made, with its parents
    assert og.main([str(source_file(tmp_path, rows)), "--seed", "7", "--out", str(out)]) == 0
    assert capsys.readouterr().out == (
        f"champions.csv: 3 rows\n"
        f"controls.csv: 3 rows, seed 7\n"
        f"left-out.csv: 3 rows (no birth time 1, no coordinates 1, more than 3 hours 1)\n"
        f"written to {out}\n"
    )  # the reasons counted in the order they're checked
    assert [row["subject"] for row in read_csv(out / "champions.csv")] == ["A1-1", "A1-3", "A1-5"]
    assert len(read_csv(out / "controls.csv")) == 3
    assert read_csv(out / "left-out.csv") == [
        {"GQID": "A1-2", "reason": "no birth time"},
        {"GQID": "A1-4", "reason": "no coordinates"},
        {
            "GQID": "A1-6",
            "reason": "more than 3 hours: local time minus universal time is +08:00",
        },
    ]


def test_the_command_won_t_overwrite_a_file(tmp_path: Path) -> None:
    out = tmp_path / "converted"
    out.mkdir()
    (out / "controls.csv").write_text("an earlier run\n")
    path = source_file(tmp_path, [person(1), person(2)])
    with pytest.raises(SystemExit, match=r"controls\.csv already exists"):
        og.main([str(path), "--seed", "1", "--out", str(out)])
    assert sorted(p.name for p in out.iterdir()) == ["controls.csv"]
    assert (out / "controls.csv").read_text() == "an earlier run\n"


def test_the_command_needs_two_champions(tmp_path: Path) -> None:
    path = source_file(tmp_path, [person(1), person(2, DATE="1930-01-02")])
    out = tmp_path / "converted"
    with pytest.raises(SystemExit, match="controls need at least two champions"):
        og.main([str(path), "--seed", "1", "--out", str(out)])
    assert not out.exists()


def test_the_command_refuses_a_file_it_can_t_read(tmp_path: Path) -> None:
    path = source_file(tmp_path, [person(1)], [c for c in HEADER if c != "LAT"])
    out = tmp_path / "converted"
    with pytest.raises(SystemExit, match="the file has no LAT column"):
        og.main([str(path), "--seed", "1", "--out", str(out)])
    assert not out.exists()


# --- The import takes both files ------------------------------------------------------------


@pytest.mark.django_db
def test_the_import_takes_both_files_and_the_source_s_universal_times(tmp_path: Path) -> None:
    group = Variable.objects.create(name="group", kind=Variable.Kind.CATEGORY)
    for name in ("champion", "control"):
        Category.objects.create(variable=group, name=name)
    rows = [
        person(1),
        person(2, DATE="1890-03-02 10:00", TZO="+00:09:21", **{"DATE-UT": "1890-03-02 09:50:40"}),
        person(3, DATE="1885-06-01 08:00", TZO="", **{"DATE-UT": "1885-06-01 07:10:04"}),
        person(4, DATE="1932-08-06 24:00", TZO="", **{"DATE-UT": "1932-08-06 23:00"}),
        person(5, DATE="1880-02-02 06:00", TZO="-00:13:29", **{"DATE-UT": "1880-02-02 06:13:29"}),
    ]
    out = tmp_path / "converted"
    assert og.main([str(source_file(tmp_path, rows)), "--seed", "3", "--out", str(out)]) == 0
    source = Source.objects.create(citation="A synthetic source")

    plan = plan_import(
        read_text((out / "champions.csv").read_bytes()), source, add_to_existing=False
    )  # as import_csv reads it
    assert (plan.refused, plan.total, plan.values) == ({}, 5, 5)
    universal = {row["GQID"]: row["DATE-UT"] for row in rows}
    for planned in plan.rows:
        resolved = resolve(planned.birth_record.time, planned.birth_record.place)
        stated = universal[planned.label]
        expected = datetime.fromisoformat(stated if len(stated) > 16 else stated + ":00")
        assert resolved.utc == expected.replace(tzinfo=UTC), planned.label
        assert [value.category.name for value in planned.values if value.category] == ["champion"]

    controls = plan_import(
        read_text((out / "controls.csv").read_bytes()), source, add_to_existing=False
    )
    assert (controls.refused, controls.total, controls.values) == ({}, 5, 5)


def test_the_files_are_utf_8_csv(tmp_path: Path) -> None:
    out = tmp_path / "converted"
    og.main([str(source_file(tmp_path, [person(1), person(2)])), "--seed", "1", "--out", str(out)])
    text = (out / "champions.csv").read_bytes().decode("utf-8")
    assert not text.startswith("﻿")
    assert next(csv.reader(io.StringIO(text))) == IMPORT_COLUMNS


def test_the_csv_text_is_the_file_the_command_writes(tmp_path: Path) -> None:
    rows = [person(1), person(2), person(3)]
    out = tmp_path / "converted"
    assert og.main([str(source_file(tmp_path, rows)), "--seed", "1", "--out", str(out)]) == 0
    champions = og.import_rows(converted(tmp_path, rows).champions, "champion")
    written = (out / "champions.csv").read_bytes().decode("utf-8")  # its \r\n line ends kept
    assert og.csv_text(og.IMPORT_COLUMNS, champions) == written


def test_the_csv_text_is_rfc_4180_s() -> None:
    rows = [{"a": "1", "b": "x, y"}, {"a": "2", "b": 'say "hi"'}]
    assert og.csv_text(("a", "b"), rows) == 'a,b\r\n1,"x, y"\r\n2,"say ""hi"""\r\n'
