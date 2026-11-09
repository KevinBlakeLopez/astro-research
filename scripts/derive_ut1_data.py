"""Derive the packaged UT1 table from IERS's EOP 20 C04 series.

    curl -sSfR -o eopc04.1962-now https://hpiers.obspm.fr/iers/eop/eopc04/eopc04.1962-now
    uv run python scripts/derive_ut1_data.py eopc04.1962-now --last-modified "<HTTP date>"

`--last-modified` is the download's Last-Modified header (`curl -sI` shows it). The table keeps
two columns, the MJD and UT1 - UTC at 0h UTC, copied digit for digit. It runs from 1972-01-01
(before then civil time was UT, and IERS's values are against the old UTC) to the series' last
day. The script writes it into the package and prints the manifest entry to paste into
src/astro_research/ephemeris_manifest.toml.

IERS revises its most recent values in later releases, so each download gives a new table and
hash. A new table changes every calculation key, since its hash is part of the provenance.
"""

import argparse
import hashlib
import sys
from datetime import date, timedelta
from pathlib import Path

SOURCE_URL = "https://hpiers.obspm.fr/iers/eop/eopc04/eopc04.1962-now"
TABLE = Path(__file__).resolve().parents[1] / "src" / "astro_research" / "iers_eop20c04_ut1.txt"
FIRST_DAY = date(1972, 1, 1)
MJD_EPOCH = date(1858, 11, 17)


def main() -> int:
    parser = argparse.ArgumentParser(description="Derive the packaged UT1 table from IERS C04.")
    parser.add_argument("source", type=Path, help="a downloaded copy of eopc04.1962-now")
    parser.add_argument("--last-modified", required=True, help="the download's Last-Modified")
    args = parser.parse_args()
    source_bytes = args.source.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()

    rows: list[tuple[int, str]] = []
    for line in source_bytes.decode("ascii").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        # YR MM DD HH MJD x y UT1-UTC ...
        year, month, day, hour, mjd, _x, _y, ut1_minus_utc, *_ = line.split()
        when = date(int(year), int(month), int(day))
        if when < FIRST_DAY:
            continue
        if hour != "0" or float(mjd) != (when - MJD_EPOCH).days:
            print(f"unexpected row, not 0h UTC on its MJD: {line!r}", file=sys.stderr)
            return 1
        if rows and int(float(mjd)) != rows[-1][0] + 1:
            print(f"a day is missing before {when}", file=sys.stderr)
            return 1
        rows.append((int(float(mjd)), ut1_minus_utc))
    if not rows or rows[0][0] != (FIRST_DAY - MJD_EPOCH).days:
        print(f"the series does not start by {FIRST_DAY}", file=sys.stderr)
        return 1
    last_day = MJD_EPOCH + timedelta(days=rows[-1][0])

    header = [
        "# UT1 - UTC at 0h UTC, one day per line: the MJD, then UT1 - UTC in seconds.",
        "# From the IERS EOP 20 C04 series, consistent with ITRF 2020, of the IERS Earth",
        "# Orientation Parameters Product Centre at Paris Observatory.",
        f"# Source: {SOURCE_URL}",
        f"# Source SHA-256: {source_sha256}",
        f"# Source Last-Modified: {args.last_modified}",
        f"# Days: {FIRST_DAY} to {last_day}, copied unchanged by scripts/derive_ut1_data.py.",
    ]
    text = "\n".join([*header, *(f"{mjd} {value}" for mjd, value in rows)]) + "\n"
    data = text.encode("ascii")
    TABLE.write_bytes(data)
    print(f"wrote {TABLE} ({len(rows)} days, {len(data)} bytes)\n")
    print("[ut1_data]")
    print(f'name = "{TABLE.name}"')
    print(f'source_url = "{SOURCE_URL}"')
    print(f'source_sha256 = "{source_sha256}"')
    print(f'source_last_modified = "{args.last_modified}"')
    print(f'first_day = "{FIRST_DAY}"')
    print(f'last_day = "{last_day}"')
    print(f"size = {len(data)}")
    print(f'sha256 = "{hashlib.sha256(data).hexdigest()}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
