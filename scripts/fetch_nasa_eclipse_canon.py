"""Fetch NASA's eclipse catalogue rows for 1901-2100, used by tests/test_eclipses.py.

    uv run python scripts/fetch_nasa_eclipse_canon.py

Writes tests/fixtures/nasa_eclipse_canon.json: every solar and lunar eclipse from 1901 to 2100 in
the Five Millennium Catalogs (Espenak and Meeus, NASA GSFC), with the instant of greatest eclipse
in TD (= TT), the type, saros series, gamma and magnitudes, copied as printed.

NASA grants permission to reproduce the data with an acknowledgment, which the fixture carries.
Run once and commit the output; the tests read the file offline. Rerun only deliberately and
review the diff.
"""

import html
import json
import re
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

SITE = "https://eclipse.gsfc.nasa.gov"
PAGES = {
    "solar": ["/SEcat5/SE1901-2000.html", "/SEcat5/SE2001-2100.html"],
    "lunar": ["/LEcat5/LE1901-2000.html", "/LEcat5/LE2001-2100.html"],
}
ACKNOWLEDGMENT = {
    "solar": "Eclipse Predictions by Fred Espenak (NASA's GSFC)",
    "lunar": "Eclipse Predictions by Fred Espenak and Jean Meeus (NASA's GSFC)",
}
OUTPUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "nasa_eclipse_canon.json"
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MONTHS = {name: number for number, name in enumerate(MONTH_NAMES, 1)}
ROW = re.compile(r"^\s*(\d{5})\s+(\d{4}) (\w{3}) (\d\d)\s+(\d\d:\d\d:\d\d)\s+(.*)$")
# After the date and time, both tables continue with: delta-T, lunation, saros, type, qualifier,
# gamma, then the solar magnitude, or the lunar penumbral and umbral magnitudes.
SOLAR_FIELDS = ("delta_t_s", "lunation", "saros", "type", "qualifier", "gamma", "magnitude")
LUNAR_FIELDS = (
    "delta_t_s",
    "lunation",
    "saros",
    "type",
    "qualifier",
    "gamma",
    "penumbral_magnitude",
    "umbral_magnitude",
)


def fetch(path: str) -> str:
    with urllib.request.urlopen(SITE + path, timeout=120) as response:
        text: str = response.read().decode("latin-1")
    return text


def rows(page: str, fields: tuple[str, ...]) -> list[dict[str, str]]:
    found = []
    for block in re.findall(r"<pre>(.*?)</pre>", page, flags=re.S | re.I):
        for line in html.unescape(re.sub(r"<[^>]+>", "", block)).splitlines():
            match = ROW.match(line)
            if match is None:
                continue
            catalog, year, month, day, td, rest = match.groups()
            values = rest.split()[: len(fields)]
            found.append(
                {
                    "catalog": catalog,
                    "date": f"{year}-{MONTHS[month]:02d}-{day}",
                    "td_greatest": td,
                    **dict(zip(fields, values, strict=True)),
                }
            )
    return found


def main() -> int:
    canon: dict[str, list[dict[str, str]]] = {}
    for kind, paths in PAGES.items():
        fields = SOLAR_FIELDS if kind == "solar" else LUNAR_FIELDS
        canon[kind] = [row for path in paths for row in rows(fetch(path), fields)]
        years = sorted({row["date"][:4] for row in canon[kind]})
        print(f"{kind}: {len(canon[kind])} eclipses, {years[0]}-{years[-1]}")
        if years[0] != "1901" or years[-1] != "2100":
            print(f"unexpected year range for {kind}", file=sys.stderr)
            return 1
    fixture = {
        "description": "Five Millennium Catalogs of Solar and Lunar Eclipses (Espenak and Meeus, "
        "NASA GSFC), 1901-2100. td_greatest is the instant of greatest eclipse in Terrestrial "
        "Dynamical Time (= TT), rounded to the second; values are copied as printed. The type "
        "keeps NASA's suffix, e.g. 'T-' (non-central total).",
        "acknowledgment": ACKNOWLEDGMENT,
        "retrieved_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pages": [SITE + path for paths in PAGES.values() for path in paths],
        **canon,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(fixture, indent=1) + "\n")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
