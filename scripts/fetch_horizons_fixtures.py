"""Fetch the JPL Horizons reference values used by tests/test_reference_horizons.py.

    uv run python scripts/fetch_horizons_fixtures.py

Writes tests/fixtures/horizons_astrometric.json: geocentric astrometric RA/Dec (ICRF, corrected
for light-time only; Horizons quantity 1) and the light-time-corrected distance (quantity 20) at
fixed TT instants. That frame involves no precession-nutation model, so it compares ephemeris
data and light-time directly. The distance lets the layer 3 reference pipeline compute light
deflection without taking anything from Swiss Ephemeris.

Run once and commit the output; the tests read the file offline. Rerun only deliberately, e.g.
when the fixture dates or bodies change, and review the diff.
"""

import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

API = "https://ssd.jpl.nasa.gov/api/horizons.api"
OUTPUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "horizons_astrometric.json"

# Horizons target codes. Jupiter-Pluto (and Mars) are system barycenters, which is what Swiss
# Ephemeris returns by default (observed: Pluto matches 9 to 0.6 mas, 999 is 76 mas off).
BODIES = {
    "Sun": "10",
    "Moon": "301",
    "Mercury": "199",
    "Venus": "299",
    "Mars": "4",
    "Jupiter": "5",
    "Saturn": "6",
    "Uranus": "7",
    "Neptune": "8",
    "Pluto": "9",
}
# 12:00 TT on each date. 1800-01-15 and 1800-03-15 fall where the 2026/04/14 data build returned a
# wrong Mercury; 2399-12-15 is near the end of the pinned files.
DATES = [
    "1800-01-15",
    "1800-03-15",
    "1850-01-01",
    "1900-01-01",
    "1950-01-01",
    "2000-01-01",
    "2024-07-01",
    "2100-01-01",
    "2399-12-15",
]


def julian_day_tt(date: str) -> float:
    """Julian day at 12:00 on a Gregorian date (the time scale is set in the request)."""
    year, month, day = (int(part) for part in date.split("-"))
    a = (14 - month) // 12
    y, m = year + 4800 - a, month + 12 * a - 3
    return day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045 + 0.0


def query(command: str, jds: list[float]) -> tuple[dict[str, str], list[str]]:
    params = {
        "format": "json",
        "COMMAND": f"'{command}'",
        "OBJ_DATA": "'NO'",
        "MAKE_EPHEM": "'YES'",
        "EPHEM_TYPE": "'OBSERVER'",
        "CENTER": "'500@399'",
        "TIME_TYPE": "'TT'",
        "TLIST_TYPE": "'JD'",
        "TLIST": "'" + " ".join(f"{jd:.1f}" for jd in jds) + "'",
        "QUANTITIES": "'1,20'",
        "ANG_FORMAT": "'DEG'",
        "EXTRA_PREC": "'YES'",
        "CSV_FORMAT": "'YES'",
    }
    with urllib.request.urlopen(API + "?" + urllib.parse.urlencode(params), timeout=120) as r:
        payload = json.load(r)
    if "error" in payload:
        raise SystemExit(f"Horizons error for {command}: {payload['error']}")
    return payload["signature"], payload["result"].splitlines()


def main() -> int:
    jds = [julian_day_tt(d) for d in DATES]
    bodies = {}
    signature: dict[str, str] = {}
    for name, command in BODIES.items():
        signature, lines = query(command, jds)
        source = re.search(r"Target body name:.*\{source: (\S+)\}", "\n".join(lines))
        start = lines.index("$$SOE")
        rows = lines[start + 1 : lines.index("$$EOE")]
        if source is None or len(rows) != len(jds):
            print(f"unexpected response for {name}: {len(rows)} rows", file=sys.stderr)
            return 1
        header = [h.strip() for h in lines[start - 2].split(",")]
        values = []
        for date, jd, row in zip(DATES, jds, rows, strict=True):
            cells = dict(zip(header, (c.strip() for c in row.split(",")), strict=False))
            values.append(
                {
                    "date": date,
                    "jd_tt": jd,
                    "ra_deg": cells["R.A.___(ICRF)"],
                    "dec_deg": cells["DEC____(ICRF)"],
                    "delta_au": cells["delta"],
                }
            )
        bodies[name] = {"command": command, "source": source.group(1), "rows": values}
        print(f"{name:8} {command:>4}  {source.group(1)}  {len(values)} rows")

    fixture = {
        "description": "Geocentric astrometric RA/Dec, ICRF, corrected for down-leg light-time "
        "only (JPL Horizons quantity 1), and distance delta in AU at the light-time-corrected "
        "instant (quantity 20), at 12:00 TT. Values are copied as printed.",
        "retrieved_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "api": API,
        "api_signature": signature,
        "request": {
            "EPHEM_TYPE": "OBSERVER",
            "CENTER": "500@399",
            "TIME_TYPE": "TT",
            "QUANTITIES": "1,20",
            "ANG_FORMAT": "DEG",
            "EXTRA_PREC": "YES",
        },
        "bodies": bodies,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(fixture, indent=1) + "\n")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
