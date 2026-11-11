"""python -m astro_research.build_record PATH

Checks swisseph's module byte for byte against pysweph's install record and writes a build
record of what matched to PATH, or writes nothing and exits 1 if it doesn't match. A Mac app's
build runs this after its packages are installed and before they're signed; the app's launcher
then hands the record to `ephemeris.use_build_record`.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from astro_research.ephemeris import BindingMismatch, write_build_record


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m astro_research.build_record",
        description="Check swisseph's module against pysweph's install record, and record it.",
    )
    parser.add_argument("path", type=Path, help="where to write the build record")
    args = parser.parse_args(argv)
    try:
        record = write_build_record(args.path)
    except BindingMismatch as error:
        print(f"no build record written: {error}", file=sys.stderr)
        return 1
    print(f"wrote {args.path}: pysweph {record.pysweph}, {record.module} as pysweph recorded it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
