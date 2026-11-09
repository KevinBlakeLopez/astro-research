"""Compute one chart and print it.

    uv run python scripts/chart.py --local 1987-06-15T04:25 --zone Europe/London \\
        --lat 51.5074 --lon -0.1278 --time-scale clock-as-ut1 --aspects major,octile

Prints the input, the resolved time (with the calculation key), positions, angles and every pair
measurement. Every value is unrounded engine output shown to 8 decimals of a degree. A record the
engine refuses (an ambiguous local time, say) is reported as an error, with exit status 2.
"""

import argparse
import sys

from _cli import (
    REFUSALS,
    add_record_options,
    add_settings_options,
    degrees,
    offset_text,
    record_from,
    settings_from,
    table,
    utc_text,
)

from astro_research.chart import Chart, compute_chart
from astro_research.ephemeris import Ephemeris


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute one chart and print it.")
    add_record_options(parser)
    add_settings_options(parser)
    args = parser.parse_args(argv)
    try:
        record = record_from(parser, args)
        settings = settings_from(parser, args)
        chart = compute_chart(Ephemeris.open(args.ephe), record, settings)
    except REFUSALS as error:
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(render(chart))
    return 0


def render(chart: Chart) -> str:
    time, angles, settings = chart.time, chart.angles, chart.settings
    instant = time.instant
    zone = (
        f", {instant.zone_abbreviation}, tzdata {instant.tzdata_version}"
        if instant.tzdata_version
        else ""
    )
    sections = [
        "== Input",
        f"time             {chart.record.time!r}",
        f"place            {chart.record.place!r}",
        f"time scale       {settings.time_scale.value}",
        f"aspect angles    {', '.join(f'{a:g}' for a in settings.aspect_angles)}",
        "",
        "== Time",
        f"utc              {utc_text(instant.utc)} (offset {offset_text(instant.utc_offset)}, "
        f"{instant.offset_source.value}{zone})",
        f"jd ut1           {time.jd_ut1!r}",
        f"jd tt            {time.jd_tt!r}",
        f"delta t s        {time.delta_t_s:.6f}",
        f"ut1 - utc s      {time.ut1_minus_utc_s:.6f}",
        f"ut1 basis        {time.ut1_basis.value}",
        f"calculation key  {chart.calculation_key}",
        "",
        "== Positions (degrees; speeds per day; distance in AU)",
        table(
            [
                "point",
                "longitude",
                "latitude",
                "ra",
                "declination",
                "lon speed",
                "dec speed",
                "distance",
            ],
            [
                [
                    p.body.value,
                    degrees(p.longitude),
                    degrees(p.latitude),
                    degrees(p.right_ascension),
                    degrees(p.declination),
                    degrees(p.longitude_speed),
                    degrees(p.declination_speed),
                    f"{p.distance_au:.9f}",
                ]
                for p in chart.positions.bodies
            ],
        ),
        "",
        "== Angles (degrees; speeds per day)",
        table(
            ["angle", "longitude", "speed"],
            [
                ["armc", degrees(angles.armc), degrees(angles.armc_speed)],
                ["ascendant", degrees(angles.ascendant), degrees(angles.ascendant_speed)],
                ["midheaven", degrees(angles.midheaven), degrees(angles.midheaven_speed)],
                ["descendant", degrees(angles.descendant), ""],
                ["imum_coeli", degrees(angles.imum_coeli), ""],
                ["vertex", degrees(angles.vertex), degrees(angles.vertex_speed)],
            ],
        ),
        f"true obliquity   {degrees(angles.true_obliquity)}",
        f"beyond polar circle  {angles.beyond_polar_circle}",
        "",
        "== Pairs (degrees; deviation = separation - aspect angle)",
        table(
            [
                "a",
                "b",
                "b - a",
                "separation",
                *(f"dev {a:g}" for a in settings.aspect_angles),
                "dec diff",
                "dec sum",
            ],
            [
                [
                    m.a.point.value,
                    m.b.point.value,
                    degrees(m.longitude_difference),
                    degrees(m.separation),
                    *(degrees(d.deviation) for d in m.deviations),
                    degrees(m.declination_difference),
                    degrees(m.declination_sum),
                ]
                for m in chart.pairs
            ],
        ),
    ]
    return "\n".join(sections)


if __name__ == "__main__":
    sys.exit(main())
