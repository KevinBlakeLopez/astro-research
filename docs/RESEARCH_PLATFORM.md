# Research platform

`research-platform/` stores subjects, sources, birth records and datasets in PostgreSQL, and runs the engine over datasets. It's a Django project and a separate distribution, `research-platform`, which depends on the engine. The engine doesn't depend on it: no `astro_research` module imports Django, psycopg or the platform, and the engine's distribution depends on neither (`tests/test_boundary.py`).

Django owns the schema (`research_platform/models.py` and its migrations). The web interface, described below, is the admin for entering data; a chart page, a birth-time sensitivity page, a nearest-eclipses page and a parans page for each record; and a comparison of any two records.

## What is stored

| Model | Holds |
|---|---|
| `Subject` | A label you choose, unique, and notes. Nothing personal unless the research needs it. |
| `Source` | A citation, the source's own reliability rating as text (no scale imposed), when it was retrieved, notes. |
| `Record` | One source's birth data for one subject, as the engine's `BirthRecord` fields. A subject can have several records, which may disagree. |
| `Dataset` | A unique name, a description, and the records it uses. It chooses records, not subjects, so which record stands for a subject is explicit. A dataset may hold several records of one subject; whether to forbid that is a research rule. |
| `CalculationRun` | One run over a dataset: its chart settings, engine version and ephemeris provenance, its status, start and end, and how many charts it computed, reused and saw refused. |
| `RunOutcome` | What a run made of one record: its stored chart, or the engine's refusal (class and message). One per record per run, and always exactly one of the two (database constraints). |
| `StoredChart` | One chart's `chart-result-v1` document (`RESULT_FORMAT.md`), under its calculation key, shared by every run that produced it. |

Outcomes, hypotheses, analysis runs and derived configurations belong to the research layer, which comes later.

**Records, field by field.** A record's `reading` is ISO 8601 text. It's aware (`+00:00`) for UTC and the naive wall-clock reading otherwise, since a Django `DateTimeField` can't hold a naive reading while times are kept zone-aware. The other fields are `zone`, `utc_offset_us` (the offset of an offset time, or the stated offset of a zoned one), `accept_pre_1970_tzdb`, `latitude`, `longitude`, `elevation_m`, `time_resolution` and `stated_uncertainty_us`.

`Record.from_birth_record()` and `record.birth_record()` convert both ways, and a record read back is equal to the engine's. Validating a record (`full_clean()`) asks the engine to build it. So a latitude of 95° is invalid, as is a reading finer than its stated resolution, or fields that describe no single kind of civil time (a zone on a UTC record, say).

**Charts are stored as text,** not `jsonb`. On PostgreSQL 18.6, `jsonb` reorders keys, writes 0.0 for −0.0 and rewrites exponents as decimals; `json` keeps the text (observed). As text, a stored chart is exactly what the format wrote, and reads back equal to the engine's chart.

## Runs

`research_platform.runner.run_dataset(dataset, settings)` computes a chart for each of a dataset's records, in one process:

1. **The run is recorded first,** as running, with the settings, engine version and ephemeris provenance.
2. **Then each record gets an outcome, in one transaction.**
   - **Reuse:** a record's calculation key is known before anything is calculated. A chart already stored under it is reused; otherwise the chart is computed and stored.
   - **Refusals:** the engine's refusal of a record is recorded, and the run carries on. The refusals are the engine's civil-time errors, invalid coordinates or records, an instant inside a leap second, angles at the poles, and dates outside the data. That last group is `DateOutsideEphemeris` with `DateOutsideUt1Data` and `DateOutsideLeapSeconds`: the engine classes it as an environment error, but a date outside the data belongs to the record.
3. **The run ends finished,** with its counts.

**Any other error stops the run:** an environment error, such as a stale handle or wrong files, or a bug. Every outcome and chart of the run is rolled back, the run is marked failed with the error's class and message, and the error is raised again.

**One process is enough at the brief's scale.** For 200 records, a fresh process took 0.74 s in all (measured): 55 ms for imports, 24 ms to open the engine, 129 ms for the charts and 416 ms for their documents. The engine is stateless per call, so a process pool can be added to the runner later without changing it. One transaction per run suits runs of this size; larger runs would commit in chunks. Two runs storing the same chart at the same moment aren't handled: runs are started one at a time.

## Running it

The database is the PostgreSQL 18.6 of `research-platform/compose.yaml`, on `127.0.0.1:55432` with local-only credentials. Docker is needed; on this machine it runs under colima.

```bash
docker compose -f research-platform/compose.yaml up -d
```

```bash
uv sync --all-packages
```

```bash
cd research-platform && uv run python manage.py migrate
```

```bash
cd research-platform && uv run python manage.py compute_dataset NAME --aspects major,octile
```

The command prints a summary such as `run 3: 180 charts (12 computed, 168 reused), 2 refused`. `--time-scale` takes the same values as the scripts and defaults to `ut1-from-iers`. Set `RESEARCH_DB_HOST`, `RESEARCH_DB_PORT`, `RESEARCH_DB_NAME`, `RESEARCH_DB_USER` and `RESEARCH_DB_PASSWORD` to use another database, and `RESEARCH_EPHE_DIR` for another ephemeris folder.

## The web interface

It runs on this machine only: it answers on 127.0.0.1 and localhost, and every page needs a login.

- **The admin** (`/admin/`) is where subjects, sources, records and datasets are entered. Saving a record asks the engine to build it, so the engine's reason for refusing one shows on the form. Runs, outcomes and stored charts can be looked at but not added, changed or deleted. A record's admin page links to its chart.
- **A record's chart** (`/records/<id>/chart/`) is computed on request, under the settings in its address: `aspects` (degrees and/or `major`, `octile`) and `time_scale` (default `ut1-from-iers`). So an address reproduces exactly what the page shows. It shows:
  - the resolved time and where UT1 came from;
  - positions, angles and every pair, to six decimals (about 4 mas), formatted as Python formats them;
  - a link to download the chart's `chart-result-v1` document, with every value at full precision.

  **Aspect angles are never defaulted.** With none named, the pairs show separations and declinations, and no deviations.
- **The pairs table** can be narrowed to one point and sorted by deviation from one aspect angle. HTMX fetches the table again without reloading the page (`/records/<id>/chart/pairs/`).
- **Birth-time sensitivity** (`/records/<id>/sensitivity/`) runs the engine's sweep on request. It recomputes a whole chart at each offset from the recorded UTC instant, so the values are exact, not estimated from speeds.
  - **Offsets:** you name a span and a step in seconds (`span=600&step=60` is −10 to +10 minutes every minute), with the aspect angles and time scale as on the chart page. Offsets are never defaulted.
  - **Caps:** a sweep is at most 241 offsets and 7200 s either side. That's about 0.3 s of computing (extrapolated from 139 ms for 121 offsets, measured). It also stays well inside the hours it takes the Ascendant to move half a circle, past which the sweep's wrapped changes would misread.
  - **The series** are listed from the widest range of change to the narrowest, the first 100 unless you ask for all. Each shows its range in arcseconds and its rate at the recorded time, which is a local slope only. Nothing marks a series as sensitive: the range is the number.
  - **Narrowing,** with HTMX: by quantity, point, aspect angle, and whether the Earth's rotation drives it. Series with the Ascendant, MC or Vertex are rotation-driven: local sidereal time fixes all three, so for statistics they're one factor.
  - **One series' detail** shows its value and change at every offset, with a plot drawn as SVG by the server.
  - **A warning** appears when a sweep crosses Swiss Ephemeris's switch of sidereal-time model (1850-01-01 and 2050-01-01 UT1). If the engine refuses the record, or any offset's chart (for example past IERS's last day), the page shows the refusal.
  - **The CSV download** has one row per series per offset, with the columns `offset_s`, `quantity`, `point_a`, `point_b`, `aspect_angle`, `rotation_driven`, `value` and `change`.
    - `point_b` and `aspect_angle` are empty when a series has none, and `rotation_driven` is `true` or `false`.
    - Values and changes are in degrees, at full precision (Python's repr, which reads back exactly).
    - The file is named `<base calculation key>-span<span>-step<step>.csv`. The base chart's JSON document carries the provenance a CSV can't.
    - Size: 26,418 rows and 2.1 MiB for ±10 minutes every minute with the major aspect angles (measured).

  The chart page and the record's admin page link to it.
- **Nearest eclipses** (`/records/<id>/eclipses/`): the solar and lunar eclipse before and after the birth, from the engine's `previous_eclipse` and `next_eclipse`.
  - **The types** are named in the address, `solar` and `lunar`, each a comma-separated list or `all`, and are never defaulted. A kind is shown only when its types are named.
  - **Each eclipse shows:**
    - its type;
    - greatest eclipse (NASA's definition) and the syzygy in UTC, and the TT days from the birth;
    - its degree and declination: by the project's convention, taken at the syzygy, the Sun's for a solar eclipse and the Moon's for a lunar one;
    - its magnitudes and saros series and member;
    - for a solar eclipse, whether it's central and the place of greatest eclipse;
    - the Sun and Moon at both instants.
  - **Refusals:** an eclipse the engine refuses shows its refusal in its own place, and the others still show. For a birth up to about six months before IERS's last day (2026-10-10), the next eclipses are refused but the ones before still show.
  - **Not yet:** separations of an eclipse's degree from the birth chart's points. That's a new engine measurement, to be added there first.

  The chart page and the record's admin page link to it.
- **Parans** (`/records/<id>/parans/`): every rise, set and transit in the project's window, birth ±12 hours of clock time (fixed), from the engine's `angular_events`, and the gaps between events of two different points (`paran_gaps`). Each body's actual position is used, ecliptic latitude included, topocentric.
  - **Named in the address, never defaulted:**
    - `bodies`: `all` or a comma-separated list;
    - `stars`: catalogue designations without their leading comma (`alLeo,alTau`), at most 100;
    - the horizon: `horizon=geometric` (disc centre, no refraction) or `horizon=almanac` (upper limb, 1013.25 hPa, 10 °C), or else `disc` with an optional `pressure` and `temperature` together.
  - **Stars are checked against the verified catalogue exactly,** since Swiss Ephemeris's own lookup also finds `zePscB`, which the file doesn't have (observed). A search by name finds stars, and each result is a link that adds the star to those already named.
  - **What it shows:** the events in time order, with their UTC instants and the ARMC, narrowed to one point if you like, and the bodies and stars Swiss Ephemeris reports as not rising or setting. Then the gaps, closest first, the first 100, narrowed by point or by kind (star–body, star–star, body–body). No orb is applied: how close counts is yours to decide.
  - **The CSV download** lists every gap, with columns `first_point`, `first_event`, `first_utc`, `second_point`, `second_event`, `second_utc`, `seconds` and `armc`. Points are body names or designations, and values are at full precision.
  - **Cost:** 50 stars and the ten bodies take about 30 ms and give 19,054 gaps (measured).
  - **Refusals:** a refused record or window (the pole, past IERS's last day, outside the data files) shows its refusal; an unknown star, body or horizon shows the reason above the form.

  The chart page and the record's admin page link to it.
- **Comparing two records** (`/records/<a>/compare/<b>/`): twins, or two sources' records of one subject. Choose two records in the admin's record list and run **Compare the two selected records**; the older record is A, and the page can swap them.
  - **Both charts use the settings in the address,** as the engine's `compare()` requires, and every number is B minus A.
  - **It shows** both UTC readings, the civil and elapsed seconds between them, and the ARMC change. Then each point's longitude in A and B, with its changes. Then each pair's separation in A and B, its change, and A's and B's deviation from each named aspect angle.
  - **Units:** longitudes and deviations are in degrees to six decimals, and changes in arcseconds to three decimals (1 mas). A deviation changes by exactly the separation's change, whatever the angle, so that change is shown once.
  - **Changes are continuous numbers.** No orb is applied, so nothing says a contact appeared or disappeared.
  - **The pairs** can be narrowed to those involving an angle (the Ascendant, MC and Vertex, which turn together) or one point. They can be sorted by the size of their change, or by the closer of A's and B's deviations from one aspect angle, which finds contacts that are near-exact in either chart.
  - **Each record's chart and JSON document** are a link away, with the same settings. If the engine refuses either record, the page shows that record's refusal instead of a comparison.
- **Refusals.** Settings, offsets or a filter a page can't use are refused with a 400, and the reason is shown where it happened, with debug mode on or off:
  - **on a page:** above its forms, which keep what was submitted, with no results;
  - **in a fragment:** in place of the table;
  - **in a download:** as plain text.

  Django's own handling would show a bare "Bad Request (400)" with debug mode off and a traceback page with it on (both observed). HTMX swaps no 4xx response by default (read in its source), so the base page tells it to swap in 400 and 422 responses. A record the engine refuses shows the refusal (class and message) where the chart would be, and its download returns 422.
- **HTMX** 2.0.11 is served from the platform's static files, never from another site. It was taken from the npm package, after the package matched the registry's SHA-512, and its SHA-256 is pinned in the tests.
- **The session-signing key** comes from `RESEARCH_SECRET_KEY`. Otherwise it's made on first use in `research-platform/.secret_key`, which only you can read and git ignores.

**Running it.** With the database running (see above):

```bash
cd research-platform && uv run python manage.py migrate
```

```bash
cd research-platform && uv run python manage.py createsuperuser
```

```bash
cd research-platform && RESEARCH_DEBUG=1 uv run python manage.py runserver 127.0.0.1:8000
```

`RESEARCH_DEBUG=1` lets the development server serve the static files (HTMX), and shows Django's error pages; it's for this machine only. Then open http://127.0.0.1:8000/admin/ and log in.

**Checking it in Firefox.** The automated tests check the pages' content through Django's test client, not in a browser. These steps check what only a browser shows:
1. Add a subject, a source and a record. For example, a zoned local time, reading `1987-06-15T04:25:00`, zone `Europe/London`, latitude `51.5074`, longitude `-0.1278`.
2. On the record's admin page, follow **Chart**. Expect the time, positions, angles and pairs tables, with no deviation columns and a note asking for aspect angles.
3. Enter `major,octile` as the aspect angles and press **Show**. Expect deviation columns for 0°, 45°, 60°, 90°, 120°, 135° and 180°, and an address ending in `?aspects=major%2Coctile&time_scale=ut1-from-iers`.
4. Choose `sun` under **Involving**. Expect the table to shrink to the Sun's 16 pairs without the page reloading. Firefox's Network panel shows one request to `/chart/pairs/`.
5. Choose **by deviation from** and `90°`. Expect the Sun's pairs ordered from the closest to a square to the farthest.
6. Follow **Download the JSON document**. Expect a file named after the calculation key shown on the page.
7. Add a second record at `1987-10-25T01:30:00`, `Europe/London`, and open its chart. Expect `AmbiguousLocalTime` and the engine's message instead of the tables.
8. Switch the system to dark mode. Expect the pages to follow it.
9. Add a second subject with a record a minute after the first: reading `1987-06-15T04:26:00`, the same zone and place. In the admin's record list, tick that record and the first, choose **Compare the two selected records**, and press **Go**. Expect a page titled "B minus A" with the time, points and pairs tables, and `60.000000` civil seconds.
10. Enter `major` as the aspect angles and press **Show**. Expect A and B columns for each angle.
11. Choose **an angle (Ascendant, MC, Vertex)** under **Involving**, then **by size of change**. Expect 45 pairs, all with an angle, and the largest changes first, without the page reloading.
12. Follow **Swap A and B**. Expect `-60.000000` civil seconds, and the aspect angles kept in the address.
13. In the admin, tick just one record and choose **Compare the two selected records**. Expect the message "Choose exactly two records to compare."
14. On the first record's chart page, follow **Birth-time sensitivity**. Expect a request for a span and a step, and no series.
15. Enter `600` and `60` and press **Sweep**. Expect 21 offsets and a list of the first 100 series, widest range first, led by pairs with the Ascendant.
16. Choose **none** under **Rotation-driven**. Expect the list to change without the page reloading, and no series with the Ascendant, MC or Vertex.
17. Press **Detail** on any series. Expect a line plot and a table of 21 offsets from −600 to 600.
18. Follow **Download every row as CSV**. Expect a file named `<calculation key>-span600-step60.csv` of about 2 MB that opens in a spreadsheet or pandas.
19. Enter a span of `7200` and a step of `50` and press **Sweep**. Expect the sensitivity page again, with `7200` and `50` still in the form and "a sweep is at most 241 offsets" above it.
20. On the first record's chart page with aspect angles named, open Firefox's console and run `htmx.ajax('GET', location.pathname + 'pairs/?aspects=major&point=chiron', '#pairs')`. Expect the pairs table replaced by "That can't be shown: point 'chiron' is not one of this chart's points". HTMX would leave the table as it was without the base page's setting.
21. From the first record's chart page, follow **Nearest eclipses**. Expect a request for eclipse types and no eclipses.
22. Enter `all` for both kinds and press **Show**. Expect four sections: the hybrid solar eclipse of 1987-03-29 and the annular one of 1987-09-23, and the penumbral lunar eclipses of 1987-04-14 and 1987-10-07. Each shows its degree at the syzygy.
23. Enter `ring` as the solar types and press **Show**. Expect the reason above the form, with `ring` still in it.
24. From the first record's chart page, follow **Parans**. Expect a request for bodies or stars and a horizon, and no events.
25. Type `regul` in **Find a star by name**. Expect Regulus (alLeo), magnitude 1.40, with an **Add** link, without the page reloading. Follow **Add**.
26. Enter `all` as the bodies, choose **geometric** as the horizon, and press **Show**. Expect the window's start and end, "Regulus,alLeo", the events in time order, and the closest 100 gaps.
27. Under **Gaps**, choose **star-body** as the kind. Expect only gaps between Regulus and a body, without the page reloading.
28. Follow **Download every gap as CSV**. Expect `parans-record-<id>.csv`.
29. Enter `zePscB` as a star and press **Show**. Expect "'zePscB' is not in the star catalogue" above the form.

## Tests

The engine's tests (`uv run pytest` from the repository root) need no database. The platform's tests need the database and the ephemeris files, and fail rather than skip without them:

```bash
cd research-platform && uv run pytest
```

```bash
cd research-platform && uv run mypy
```

Their records are synthetic.
