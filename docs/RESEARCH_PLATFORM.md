# Research platform

`research-platform/` stores subjects, sources, birth records and datasets in PostgreSQL, and runs the engine over datasets. It's a Django project and a separate distribution, `research-platform`, which depends on the engine. The engine doesn't depend on it: no `astro_research` module imports Django, psycopg or the platform, and the engine's distribution depends on neither (`tests/test_boundary.py`).

There's no web interface yet. Django owns the schema (`research_platform/models.py` and its migrations), so a web interface added later uses the same models.

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

## Tests

The engine's tests (`uv run pytest` from the repository root) need no database. The platform's tests need the database and the ephemeris files, and fail rather than skip without them:

```bash
cd research-platform && uv run pytest
```

```bash
cd research-platform && uv run mypy
```

Their records are synthetic.
