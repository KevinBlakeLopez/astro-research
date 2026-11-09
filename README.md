# astro-research

A headless Python engine for exploratory astrology research. It computes astronomical positions and raw measurements on Swiss Ephemeris, validated against independent references, and fails closed when an input or its environment can't be trusted.

The engine itself has no web application, database or GUI. The research platform in `research-platform/`, a separate Django package, stores subjects, sources, records and datasets in PostgreSQL, runs the engine over them, and has a local web interface: the admin, and a chart page for each record.

## What it computes

- **Positions:** the Sun, Moon, Mercury to Pluto, the mean and true lunar node, and the mean and osculating lunar apogee. Each has ecliptic and equatorial coordinates, distance and speeds.
- **Angles:** Ascendant, MC, Descendant, IC and Vertex, with the ARMC.
- **Raw measurements** between every two points: separations, deviations from any aspect angles you choose (for example the octile series 45°, 90°, 135° and 180°), parallels and contra-parallels. There are no orbs and no scores.
- **Charts** with a deterministic calculation key, written as JSON documents that read back exactly.
- **Two-chart comparisons.**
- **Birth-time sensitivity sweeps.**
- **The nearest solar and lunar eclipses** before and after a birth.
- **A proof of concept for fixed-star parans:** angular events and the gaps between them.

Civil times come in four forms: UTC, a local time with an IANA zone, a local time with a stated offset, or local mean time. Ambiguous or nonexistent local times are refused, never guessed. Which aspect angles count is always yours to name. UT1 comes by default from IERS's measured values, up to 2026-10-10; after that, name another time scale (`--time-scale`): Swiss Ephemeris's ΔT model up to 2033-09-16, the clock reading beyond.

## Documents

- [docs/CALCULATION_CONVENTIONS.md](docs/CALCULATION_CONVENTIONS.md): every convention, its alternative, and how it was validated.
- [docs/DEPENDENCY_ASSESSMENT.md](docs/DEPENDENCY_ASSESSMENT.md): Swiss Ephemeris, its bindings, Immanuel, Morinus, Valens, Astrolog and Planetdance.
- [docs/RESULT_FORMAT.md](docs/RESULT_FORMAT.md): charts as JSON documents (`chart-result-v1`), read back exactly.
- [docs/RESEARCH_PLATFORM.md](docs/RESEARCH_PLATFORM.md): the research platform: what it stores, how runs work, its web interface, and how to run it and its tests.
- [docs/PHASE1_REPORT.md](docs/PHASE1_REPORT.md): what was built and tested, the results, limitations and recommendations.

## Setup

**Requirements:**
- macOS, tested on Apple Silicon;
- [uv](https://docs.astral.sh/uv/), tested with 0.13.6 (e.g. `brew install uv`).

uv installs Python 3.13 (pinned in `.python-version`) if it's missing.

Install the locked dependencies, including the development tools:

```bash
uv sync --locked
```

That installs the engine only. For the research platform too, add `--all-packages`, and see [docs/RESEARCH_PLATFORM.md](docs/RESEARCH_PLATFORM.md) for its database.

Fetch the pinned Swiss Ephemeris data files into `ephe/`:

```bash
uv run python scripts/fetch_ephemeris.py
```

- **What it fetches:** the DE441 planet and Moon files, plus the fixed-star catalogue, from a pinned commit of the Swiss Ephemeris repository. About 1.9 MB.
- **How it checks them:** each file's size and SHA-256 are checked against `src/astro_research/ephemeris_manifest.toml` before it's moved into place.
- **What else it verifies:** the whole environment, the same check the engine runs whenever it opens the data folder.
- **Not committed:** the files aren't in the repository, and a missing or altered file stops the engine and the tests.

The IERS UT1 table isn't fetched: it ships in the package as `src/astro_research/iers_eop20c04_ut1.txt`, pinned in the same manifest. `scripts/derive_ut1_data.py` rebuilds it from a new download of IERS's series, which changes every calculation key.

## Tests and checks

```bash
uv run pytest
```

```bash
uv run ruff check
```

```bash
uv run ruff format --check
```

```bash
uv run mypy
```

The tests run offline. Their reference values are committed in `tests/fixtures/`: JPL Horizons positions and Moon vectors, and NASA's eclipse canon for 1901–2100. The scripts that fetched them are in `scripts/`. If `ephe/` is missing, the tests fail rather than skip.

## Running the engine

**One chart:** the input, the resolved time with the calculation key, positions, angles and every pair measurement.

```bash
uv run python scripts/chart.py --local 1987-06-15T04:25 --zone Europe/London --lat 51.5074 --lon -0.1278 --aspects major,octile
```

**A two-chart comparison**, B minus A (here, twins born a minute apart): the time differences, every point's change, and each contact with the Ascendant, MC or Vertex.

```bash
uv run python scripts/compare.py --a-local 1987-06-15T04:25 --a-zone Europe/London --a-lat 51.5074 --a-lon -0.1278 --b-local 1987-06-15T04:26 --b-zone Europe/London --b-lat 51.5074 --b-lon -0.1278 --aspects major,octile
```

**Options** (both scripts):

| Option | Meaning |
|---|---|
| `--utc` | A UTC instant. |
| `--local` | A wall-clock reading, with exactly one of `--zone`, `--offset` or `--local-mean-time`. |
| `--offset=-05:00` | The stated UTC offset. Negative offsets need the `=`, or they're read as options. |
| `--stated-offset` | Settles a local time that happened twice. |
| `--time-scale` | `ut1-from-iers` (the default), `ut1-from-delta-t` or `clock-as-ut1`. |
| `--aspects` | Angles in degrees and/or the sets `major` and `octile`. Required. |

Records the engine refuses are reported as an error, with exit status 2. Run either script with `--help` for every option. The output is plain text for reading; the engine itself returns structured Python objects.

From Python, the same chart:

```python
from datetime import datetime
from pathlib import Path

from astro_research.chart import BirthRecord, ChartSettings, compute_chart
from astro_research.civil_time import ZonedLocalTime
from astro_research.ephemeris import Ephemeris
from astro_research.geo import GeoPosition
from astro_research.measurements import MAJOR, OCTILE

eph = Ephemeris.open(Path("ephe"))
record = BirthRecord(
    time=ZonedLocalTime(local=datetime(1987, 6, 15, 4, 25), zone="Europe/London"),
    place=GeoPosition(latitude=51.5074, longitude=-0.1278),
)
settings = ChartSettings(aspect_angles=tuple(sorted({*MAJOR, *OCTILE})))
chart = compute_chart(eph, record, settings)
```

As a JSON document, and back:

```python
from astro_research.result_format import chart_from_json, chart_to_json

text = chart_to_json(chart)
assert chart_from_json(text) == chart
```

## Licence

**Swiss Ephemeris is dual-licensed: AGPL or the paid Swiss Ephemeris Professional License.** That covers the C library inside the pysweph binding and the data files fetched above. Using it under the AGPL places obligations on the whole project once software is distributed or a public service is run. See [docs/DEPENDENCY_ASSESSMENT.md](docs/DEPENDENCY_ASSESSMENT.md).

**This project is licensed under the GNU Affero General Public License, version 3 or later** ([LICENSE](LICENSE)), matching Swiss Ephemeris's AGPL option.

UT1 − UTC in `src/astro_research/iers_eop20c04_ut1.txt` is from the IERS EOP 20 C04 series of the IERS Earth Orientation Parameters Product Centre at Paris Observatory.

NASA's eclipse predictions in `tests/fixtures/` carry NASA's requested acknowledgments: "Eclipse Predictions by Fred Espenak (NASA's GSFC)" for solar eclipses, and "Eclipse Predictions by Fred Espenak and Jean Meeus (NASA's GSFC)" for lunar ones.
