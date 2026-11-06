"""Compare two charts and print B minus A.

    uv run python scripts/compare.py \\
        --a-local 1987-06-15T04:25 --a-zone Europe/London --a-lat 51.5074 --a-lon -0.1278 \\
        --b-local 1987-06-15T04:26 --b-zone Europe/London --b-lat 51.5074 --b-lon -0.1278 \\
        --time-scale clock-as-ut1 --aspects major,octile

Both records use the same time scale and aspect angles, as the engine requires. Prints the time
differences, every point in both charts with its change, and every contact involving the
Ascendant, MC or Vertex with both deviations. Changes are in arcseconds. A refused record is an
error, with exit status 2.
"""

import argparse
import sys

from _cli import (
    REFUSALS,
    add_record_options,
    add_settings_options,
    arcsec,
    degrees,
    record_from,
    settings_from,
    table,
    utc_text,
)

from astro_research.chart import compute_chart
from astro_research.comparison import ChartComparison, compare
from astro_research.ephemeris import Ephemeris


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two charts and print B minus A.")
    add_record_options(parser, "a-")
    add_record_options(parser, "b-")
    add_settings_options(parser)
    args = parser.parse_args(argv)
    try:
        a, b = record_from(parser, args, "a-"), record_from(parser, args, "b-")
        settings = settings_from(parser, args)
        eph = Ephemeris.open(args.ephe)
        comparison = compare(compute_chart(eph, a, settings), compute_chart(eph, b, settings))
    except REFUSALS as error:
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(render(comparison))
    return 0


def render(c: ChartComparison) -> str:
    sections = [
        "== Time",
        f"a            {utc_text(c.a.time.instant.utc)}",
        f"b            {utc_text(c.b.time.instant.utc)}",
        f"utc seconds  {c.utc_seconds!r}",
        f"tt seconds   {c.tt_seconds:.6f}",
        f"armc change  {arcsec(c.armc)} arcsec",
        "",
        "== Points (longitudes in degrees; changes in arcseconds)",
        table(
            ["point", "a longitude", "b longitude", "change", "lat change", "dec change"],
            [
                [
                    p.point.value,
                    degrees(p.a.longitude),
                    degrees(p.b.longitude),
                    arcsec(p.longitude),
                    arcsec(p.latitude),
                    arcsec(p.declination),
                ]
                for p in c.points
            ],
        ),
        "",
        "== Angle contacts (deviations in degrees; change in arcseconds, the same for every angle)",
        table(
            ["a", "b", "aspect", "a deviation", "b deviation", "change"],
            [
                [
                    pair.a.a.point.value,
                    pair.a.b.point.value,
                    f"{d.angle:g}",
                    degrees(d.a.deviation),
                    degrees(d.b.deviation),
                    arcsec(d.deviation),
                ]
                for pair in c.involving_angles()
                for d in pair.deviations
            ],
        ),
    ]
    return "\n".join(sections)


if __name__ == "__main__":
    sys.exit(main())
