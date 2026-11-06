"""Fetch the Moon's geocentric state vectors from JPL Horizons, for the layer 4 lunar-point tests.

    uv run python scripts/fetch_horizons_moon_vectors.py

Writes tests/fixtures/horizons_moon_vectors.json: the Moon's position (km) and velocity (km/s)
relative to the Earth's center, geometric (no light-time or aberration), on ICRF axes, at the
same TT instants as the astrometric fixture. The true node and osculating apogee are osculating
elements of this state, so the tests can rebuild them without Swiss Ephemeris. One request.

Run once and commit the output; rerun only deliberately and review the diff.
"""

import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from fetch_horizons_fixtures import API, DATES, julian_day_tt

OUTPUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "horizons_moon_vectors.json"
REQUEST = {
    "EPHEM_TYPE": "VECTORS",
    "COMMAND": "301",
    "CENTER": "500@399",
    "REF_PLANE": "FRAME",
    "VEC_TABLE": "2",
    "VEC_CORR": "NONE",
    "OUT_UNITS": "KM-S",
    "TIME_TYPE": "TT",
}


def main() -> int:
    jds = [julian_day_tt(d) for d in DATES]
    params = {key: f"'{value}'" for key, value in REQUEST.items()} | {
        "format": "json",
        "OBJ_DATA": "'NO'",
        "MAKE_EPHEM": "'YES'",
        "CSV_FORMAT": "'YES'",
        "TLIST_TYPE": "'JD'",
        "TLIST": "'" + " ".join(f"{jd:.1f}" for jd in jds) + "'",
    }
    with urllib.request.urlopen(API + "?" + urllib.parse.urlencode(params), timeout=120) as r:
        payload = json.load(r)
    if "error" in payload:
        print(f"Horizons error: {payload['error']}", file=sys.stderr)
        return 1
    lines = payload["result"].splitlines()
    source = re.search(r"Target body name:.*\{source: (\S+)\}", "\n".join(lines))
    start = lines.index("$$SOE")
    rows = lines[start + 1 : lines.index("$$EOE")]
    if source is None or len(rows) != len(jds):
        print(f"unexpected response: {len(rows)} rows", file=sys.stderr)
        return 1
    header = [h.strip() for h in lines[start - 2].split(",")]
    states = []
    for date, jd, row in zip(DATES, jds, rows, strict=True):
        cells = dict(zip(header, (c.strip() for c in row.split(",")), strict=False))
        states.append(
            {"date": date, "jd_tt": jd} | {k: cells[k] for k in ("X", "Y", "Z", "VX", "VY", "VZ")}
        )

    fixture = {
        "description": "Moon (301) relative to the Earth's center (500@399): geometric position "
        "(km) and velocity (km/s), ICRF axes, at 12:00 TT. Values are copied as printed.",
        "retrieved_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "api": API,
        "api_signature": payload["signature"],
        "request": REQUEST,
        "source": source.group(1),
        "states": states,
    }
    OUTPUT.write_text(json.dumps(fixture, indent=1) + "\n")
    print(f"Moon {source.group(1)}: {len(states)} states -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
