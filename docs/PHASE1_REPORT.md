# Phase 1 report

**Scope:** an astronomical calculation engine and technical feasibility spike.
**Dates:** 2026-10-31 to 2026-11-07.
**Engine:** `astro-research` 0.1.0.
**Repository state:** commit `0dff654` plus this report.

Phase 1 stops here and waits for review. Nothing beyond it (the web application, database or rules) has been started. The four decisions the report left open were made on 2026-11-09 and are recorded in section 16.

**How numbers are labelled:**
- **(test):** a bound the test suite enforces;
- **(measured):** a one-off measurement made during the work, with the method stated. Its script is not in the repository.
- **(read):** taken from source code or documentation.

## 1. What was implemented

A standalone Python package in `src/astro_research/`. Only one module, `ephemeris.py`, imports Swiss Ephemeris; every other module works on its results.

| Module | What it does |
|---|---|
| `geo.py` | Validated birthplace coordinates. Swiss Ephemeris itself accepts any value. |
| `civil_time.py` | Four kinds of civil time: UTC, a zoned local time, a local time with a stated offset, and local mean time. Each resolves to a UTC instant without guessing. Ambiguous, nonexistent and contradictory times, tzdb's local mean time, and pre-1970 tzdb rules (unless accepted) each raise a structured error. |
| `astronomical_time.py` | UT1 and TT Julian dates under a named time scale (`CLOCK_AS_UT1` or `UT1_FROM_DELTA_T`). No default. |
| `ephemeris.py` | The adapter.<br>• **Start-up check, which fails closed:** the binding's binary, the data files' hashes, that the files really loaded, and the star catalogue held in memory.<br>• **Provides:** positions, angles, eclipse primitives, rise and set, and conversion between time scales.<br>• **Refuses** dates outside the data files and stale handles. |
| `positions.py` | Apparent geocentric positions of the Sun, Moon and Mercury–Pluto, and the mean and true node and mean and osculating apogee. Each in ecliptic and equatorial coordinates, with speeds. |
| `angles.py` | Ascendant, MC, Descendant, IC, Vertex and ARMC, plus the true obliquity. |
| `measurements.py` | For every pair of points: separations, deviations from any chosen aspect angles (including the octile series), and parallel and contra-parallel measures, with rates. No orbs or scores. |
| `chart.py` | Birth records (no identities), named settings, whole charts, and a deterministic SHA-256 calculation key. |
| `comparison.py` | Two-chart comparison, B − A, keeping both charts. |
| `sensitivity.py` | Birth-time sweeps, recomputing a whole chart at each offset. |
| `eclipses.py` | The nearest solar and lunar eclipses before and after an instant, refined to NASA's definition of greatest eclipse. |
| `parans.py` | Proof of concept: every rise, set and transit in a window, and the gaps between events. |

Alongside the package:
- `scripts/`: fetching the data files and the test fixtures, plus two example scripts (one chart, and a two-chart comparison);
- `README.md`: the commands;
- `docs/CALCULATION_CONVENTIONS.md`: every convention;
- `docs/DEPENDENCY_ASSESSMENT.md`: the dependency audit.

## 2. What was tested

**791 tests** in 18 files. Each slice's tests were written before its code and confirmed failing; tests that close gaps found by mutation runs, and the one-second twins test, were added afterwards. The tests run offline from committed fixtures, and fail rather than skip if the data files are missing.

| Area | Tests | Checked against |
|---|---|---|
| Civil time, coordinates | 58 | tzdb rules, DST gaps and overlaps, New York's 1883 change from local mean time, pickling of errors |
| Astronomical time | 21 | TT = UTC + leap seconds + 32.184 s exactly; time-scale equality before 1972 |
| Environment | 18 | Binding hash, file hashes, stray and missing files, `SE_EPHE_PATH`, stale handles, the star catalogue in memory |
| Ephemeris data (layer 1) | 91 | **JPL Horizons** astrometric positions: 10 bodies × 9 dates, 1800–2399 |
| Models (layer 2) | 27 | **ERFA**: Vondrák precession, IAU 2006 obliquity, IAU 2000A nutation |
| Apparent positions (layer 3) | 90 | **Horizons + ERFA**'s apparent-place pipeline |
| Positions | 29 | Conventions pinned against Swiss Ephemeris; speeds |
| Lunar points (layer 4) | 62 | Osculating elements from **Horizons**' Moon vectors; ERFA fundamental arguments; Moon-file segment steps |
| Angles | 190 | Geometric definitions at 8 places × 5 dates, ERFA sidereal time, polar latitudes, poles |
| Measurements | 26 | Hand-built points; rates against recomputation |
| Charts and keys | 41 | Engine equivalence; key stability across processes and hash seeds; key changes |
| Comparison | 25 | B − A exactly; antisymmetry; twins a minute and a second apart; a leap second |
| Sensitivity | 26 | Recomputation; DST; leap second; the sidereal-time switch |
| Eclipses | 38 | **NASA's Five Millennium Canon**, all 909 eclipses 1901–2100; geometric definitions |
| Parans | 45 | **ERFA** `atco13` for stars; ERFA's horizon transform for bodies; window and circumpolar cases |
| Scripts | 4 | The README's commands |

The spec's fixture list is covered: ordinary modern dates, historical dates, both hemispheres, high latitudes, DST transitions, ambiguous civil times, and births one minute and one second apart. No test treats agreement with another Swiss Ephemeris-based program as validation.

**Mutation testing.** Each slice from the measurements onward was checked by deliberately breaking its code and confirming the tests caught it:

| Slice | Breakages | Caught | Survivors |
|---|---|---|---|
| Start-up check | 8 | 8 | — |
| Layer 3 engine flags | 5 flags | all | `NOGDEFL` fails 41 of 90 cases, the others 90 of 90 |
| Measurements | 10 | 10 | — |
| Charts and comparison | 50 | 49 | Subtracting UT1 instead of TT: they agree to the microsecond over a minute |
| Sensitivity | 22 | 21 | A deviation's rate swapped for the separation rate, which are identical |
| Eclipses | 33 | 30 | Three that change no result on the data checked |
| Parans | 25 | 24 | A guard that matters only for an event within milliseconds of the window start |

For the civil-time, time, positions, angles and lunar-point slices, the counts weren't recorded.

## 3. Exact dependency versions

| Component | Version |
|---|---|
| Python | 3.13.11 (pinned to 3.13 in `.python-version`; `requires-python >=3.13`) |
| uv | 0.13.6 |
| pysweph | 2.10.3.6, with Swiss Ephemeris 2.10.03 built from aloistr/swisseph `5ae0bce` |
| tzdata | 2026.6 |
| pyerfa (tests only) | 2.0.1.5, with numpy 2.5.4 |
| pytest / ruff / mypy | 9.2.0 / 0.17.3 / 2.4.1 |
| `sepl_18.se1` | DE441 build 2026/05/26, 484,061 B, SHA-256 `ca1393ce…ccd99a66` |
| `semo_18.se1` | DE441 build 2026/05/26, 1,304,771 B, SHA-256 `1ca07bd6…b582ca4f7` |
| `sefstars.txt` | 136,618 B, SHA-256 `18b0dcaf…d4f517c6` |

All three data files come from aloistr/swisseph `cae9ecd4b201544d85e411aced17660932514d43`, and full hashes are in `src/astro_research/ephemeris_manifest.toml`. `uv.lock` pins everything else, and `pyswisseph` is excluded by constraint.

**Machine:** macOS 26.6.2 on an Apple M5 Pro (arm64).

## 4. Test results

- **Full run:** 791 passed, 0 failed, 0 skipped, in 2.7 s (2026-11-07, on the machine above).
- **Fresh clone:** the same 791 passed after `uv sync --locked` and fetching the data files.
- **Lint and types:** Ruff (lint and format) and mypy `--strict` report no issues in 42 source files.
- **Reproducibility:** the example chart's calculation key, `ffd3768f…`, is identical in the fresh clone, in the main checkout, and from the README's Python example.

## 5. Numerical discrepancies

### Swiss Ephemeris against independent references

| Comparison | Largest difference | Source |
|---|---|---|
| Astrometric positions vs JPL Horizons | Mars 2.415 mas (1850-01-01, 0.62 AU from Earth); Moon 1.442 mas; others ≤ 0.78 mas | test. Budget 2 mas × max(1, r_helio/r_geo). Mars's miss corresponds to a position error of about 1.09 km. Seen from the Sun, Mars's heliocentric error is 1.12 mas (measured), so the manual's "1 milli-arcsecond" is approximate. |
| Precession vs ERFA Vondrák 2011 | 1.3105 mas (2100) | test |
| Mean obliquity vs IAU 2006 | 0.506 mas (2399) | test. Swiss Ephemeris pairs Vondrák precession with an obliquity that follows IAU 2006, not Vondrák's pole angle (5.3 mas apart at 2399). |
| Nutation vs IAU 2000A | 0.996 mas in Δψ, 0.867 mas in Δε (2100) | test. Its default is named 2000B, but the values track 2000A; against ERFA's 2000B they're 6.1 mas apart (measured). |
| Apparent positions vs Horizons + ERFA | 2.27 mas (Mars 1850) | test |
| True node and osculating apogee vs Horizons' Moon vectors | node 0.007″; apogee 0.288″ in longitude, 0.026″ in latitude | test. Requires GM(Earth + Moon); with GM(Earth) alone the apogee is 17.2° off (measured). |
| Mean node and apogee vs ERFA fundamental arguments | up to 1.068″ (2399), 0.001″ at 2000 | test |
| ARMC vs ERFA `gst06a` | ≤ 1 mas from 1850 to 2050 | test. Outside that window Swiss Ephemeris switches to its long-term model: +348.6 mas (1800), −1,794 mas (2100), +8,538 mas (2399), jumping 97.3 mas at 1850-01-01 and 1,908 mas at 2050-01-01 (measured). |
| Greatest eclipse vs NASA's canon | Swiss Ephemeris's own instants: 9.7 s solar, 10.9 s lunar. Refined to NASA's definition: 0.55 s solar, 2.75 s lunar | test (refined); measured (raw). Solar: the definitions differ. Lunar: also a coarse search, and an unexplained remainder of up to about 2 s beyond rounding. |
| Eclipse magnitudes vs NASA | central solar 0.0019; umbral 0.0008; penumbral 0.0004; non-central solar up to 0.044 | test; the non-central figure is measured and its cause not investigated |
| Place of greatest eclipse vs NASA | up to 0.96° latitude, 1.51° longitude | measured; beyond NASA's whole-degree rounding, cause not investigated |
| Star rise, set and transit vs ERFA `atco13` | first answers: 0.10 s (rise/set), 2.6 s (Polaris's transit); refined: 7.3 ms | test (refined); measured (first) |
| Body rise, set and transit vs ERFA's horizon transform | first answers: Moon 0.11 s (rise/set), 0.69 s (transit); refined: 6.8 ms | test (refined); measured (first) |
| Modelled UT1 − UTC (`UT1_FROM_DELTA_T`) vs IERS EOP 20 C04, daily 1972-01-01 to 2026-10-10 | ≤ 0.066 s for 1972–2022 (the clock convention: up to 0.81 s); 0.085 s in 2023, growing to 0.40 s by 2026-10 (the clock: ≤ 0.095 s) | measured. Swiss Ephemeris 2.10.03's ΔT table is extrapolated after 2023.0 (`swephlib.c:2426-2496`). |

### Within Swiss Ephemeris: data and behaviour

- **Data builds.** The 2026/04/14 DE441 build returned Mercury up to 103.9° wrong in early 1800, with a normal return flag (measured). The pinned 2026/05/26 build agrees with Horizons to 0.1–0.3 mas there (test).
- **DE441 vs DE431, 1900–2030:** largest differences are Neptune 0.22″, Moon 0.023″ and the osculating apogee 0.14″. Over 1800–2399, Pluto 1.7″ (measured).
- **ΔT depends on which files are open:** 18.9020 s with DE441 at 1800, against 18.6047 s with DE431 (measured).
  - `utc_to_jd` applies the DE431 value internally, so the engine always takes TT from `deltat_ex` with the pinned files.
  - Before any file is opened, ΔT is 18.4180 s at 1800-06-01 (measured). That's why a handle exists only after the start-up check has opened the files.
- **Moon-file segment boundaries** (every 27.55 days): the true node steps by up to 0.085″ and the osculating apogee by 0.91″; the Moon itself doesn't step (test).
- **Speeds aren't exact derivatives.** For slow bodies, Swiss Ephemeris's speeds differ from the rate of change of its own positions by up to 3.9 × 10⁻⁶ °/day (Pluto) (measured).
- **Estimating from speeds instead of recomputing** misses by 197″ on the Ascendant at +10 minutes, and by 4.7° for a pair passing an exact opposition (measured). This is why sensitivity sweeps recompute whole charts.
- **The time-scale convention:**
  - it moves the angles by up to about a third of a one-second birth-time difference, roughly 30″ on the Ascendant (measured);
  - within a pair of charts it cancels to 0.025″, except across a leap second, where `CLOCK_AS_UT1` loses that second (10.6″ on the Ascendant; test and measured).

## 6. Known limitations

**The time scale still has no default.** Every caller must name one until Phase 2 adds the IERS-based scale (section 16).

**Coverage:**
- **Dates:**
  - **1800-01-01 to 2400-01-10 TT** for planets (the Moon from 1799-12-23 to 2400-01-15). Everything outside is refused, including eclipses and windows that reach outside.
  - **The instant 1800-01-01 00:00 UT itself is refused,** although its TT lies inside the file; the cause is presumably light-time reaching before the start (inferred).
  - **Births before 1800** would need further data files pinned.
- **Calendar:** Gregorian dates only, from 1582-10-15.
- **Not implemented:** sidereal zodiacs, topocentric chart positions, house cusps.

**Time zones.** Historical offsets are only as good as tzdb, and tzdb is not authoritative before 1970. Some zones are links that replace a country's own history; Europe/Amsterdam, for example, is a link to Europe/Brussels. The engine refuses pre-1970 tzdb unless accepted, but checking historical offsets is the researcher's job.

**Parans:**
- **Positions:** only the topocentric actual-position definition. No zodiacal parans, and no geocentric actual-position option, because Swiss Ephemeris offers neither for rise and set.
- **Rise and set:** no circumpolar/never-rising classification, and no window helpers such as sunrise to sunrise.
- **Star catalogue check:** compares one star, so it catches a different catalogue only if that catalogue's Regulus differs.

**Eclipses:**
- the lunar residual against NASA (≤ 2.75 s), the place of greatest eclipse (≤ 1.5°) and non-central magnitudes (≤ 0.044) are unexplained;
- local circumstances and eclipse visibility at the birthplace aren't computed.

**Wrapping.** Comparison and sensitivity changes are wrapped into (−180°, 180°], so a point moving more than half a turn would be misread. That takes the Ascendant hours, not minutes.

**Guards.** Swiss Ephemeris's silent fallbacks are guarded only where the engine calls it; code calling `swisseph` directly bypasses every check.

**Not validated independently:**
- Swiss Ephemeris's modelled UT1 − UTC, against IERS's measured values;
- historical tzdb offsets;
- Swiss Ephemeris's refraction model at the horizon, since ERFA's isn't valid there, so refraction was compared only with refraction off.

**Housekeeping:**
- No machine-readable output yet: the example scripts print text, and serialisation is open.
- pysweph has no working test suite of its own, so this project's tests are the only check on it.
- About 30 one-off measurement scripts aren't in the repository.

## 7. Licensing concerns

- **Swiss Ephemeris** (the C library and the data files) is AGPL or the paid Swiss Ephemeris Professional License.
  - Under the AGPL, distributing software or running a public service obliges the whole project to be AGPL-compatible.
  - The choice must be made before either happens.
- **pysweph's wrapper code carries its own AGPL grant,** even though its `LICENSE` now holds the dual-licence notice. A Professional licence for Swiss Ephemeris might therefore not cover the binding (inferred; not legal advice).
  - The planned exit is a minimal binding of the project's own over a pinned Swiss Ephemeris commit.
  - That would matter for a non-AGPL deployment, for Swiss Ephemeris 3.0, or if pysweph lacked a wheel.
- **This project is licensed AGPL-3.0-or-later** (`LICENSE`, and `license` in `pyproject.toml`), matching Swiss Ephemeris's AGPL option and pysweph's wrapper grant. If the platform is hosted for other people, its source must be offered to them.
- **Other dependencies:**
  - tzdata is Apache-2.0;
  - pyerfa is BSD-3-Clause, and numpy BSD-3-Clause with others (test-only);
  - pytest, Ruff and mypy are MIT (development only).
- **Fixtures:**
  - NASA's eclipse predictions are reproduced with NASA's requested acknowledgments.
  - JPL Horizons output was retrieved through its public API. Its terms of use weren't reviewed (unverified).
- **Reference projects:** Morinus (GPL-3.0+), Astrolog (GPL-2.0+) and Valens (contradictory MIT/GPL) were read for reference only. No code was copied from any of them.

## 8. macOS compatibility findings

- **The engine runs natively on Apple Silicon** under macOS 26.6.2, with Python 3.13 installed by uv.
- **pysweph** has arm64 wheels for Python 3.8–3.13 only. **Python 3.14 is blocked** until a wheel appears; building its sdist is possible but untested.
- **pyswisseph** has no arm64 wheel for 3.13, but builds from source with Apple clang in about 9 s (observed). It's excluded anyway.
- **pyerfa** installs as an arm64 wheel.
- **The operating system's time-zone database** (2026e) differs from the tzdata package (2026.6), and Python's `zoneinfo` would search the system copy first. The engine always loads the package's copy explicitly.
- **The machine's default `python3` is an end-of-life 3.9.9;** it isn't used.
- **Other tools:**
  - Morinus ships only Windows binaries.
  - Valens's compiled extension has no arm64 slice.
  - Astrolog should build from source with the system compiler, without X11 (read; not attempted).
  - Planetdance is Windows-only.

## 9. Recommended calculation architecture

What Phase 1 built, and what Phase 2 should keep:

1. **Inputs are plain, validated values.** A `BirthRecord` has no identity: subjects, sources, datasets, outcomes, hypotheses and analysis runs are separate concepts for the research layer.
2. **Nothing is guessed.** Each ambiguity raises a structured error carrying what's needed to correct the record. A future batch runner can record such errors as failed rows; the engine itself stays fail-closed per calculation.
3. **One adapter owns Swiss Ephemeris.** Its path, topocentric setting and star-catalogue cache are process-wide state, and only `ephemeris.py` touches them. A verified handle refuses to work once that state changes.
4. **Every result carries its provenance:**
   - the original input;
   - the resolved instant;
   - the time scale;
   - the ephemeris files' hashes;
   - the binding;
   - the tzdata version;
   - the convention strings.
5. **The calculation key** (SHA-256 of a canonical JSON of all inputs) is the natural cache and storage key. A cache keyed on it can miss but never return a result for other inputs.
6. **Calculations are stateless functions,** so a batch system can call them repeatedly.
7. **Parallelism should use processes, one verified handle each, not threads,** because of the process-wide state (inferred; thread safety not tested).
8. **Persistence comes later.** PostgreSQL, the preferred store, would hold records, settings and results keyed by the calculation key. It needs a serialisation format first, which is a Phase 2 decision.
9. **Measurement stays separate from interpretation.** Measurements are continuous numbers with no orbs, flags or scores, and research rules belong in a layer above them.
10. **Binding exit plan:** pysweph pinned by hash, with this project's own minimal binding ready as the alternative.

## 10. Should Immanuel be adopted?

**No.** It's actively maintained, pure Python and headless, and its aspect/orb configuration is a reasonable model for a later rules layer. But for precise research:
- **Silent fallbacks:** it passes no ephemeris flags and discards Swiss Ephemeris's return flag and error text.
- **Time:** it treats UTC as UT1. It resolves an ambiguous local time to its first occurrence with only an `ambiguous` flag, and accepts nonexistent local times.
- **Missing quantities:** it gives no right ascension and no parallels.
- **Orbs:** every aspect goes through one.
- **AGPL** for the whole package.

Its astro.com-checked fixtures are consistency checks at most: they're Swiss Ephemeris-derived, not independent.

## 11. Are Morinus's algorithms worth adapting?

**Not as code.** Its definitions are worth recording, and are recorded in the dependency assessment:
- **its paran convention:** actual positions, body centre on a geometric horizon, sunrise-to-sunrise window, ±2 minutes;
- **its use of the Sun's or Moon's declination at an eclipse.**

Against adapting the code:
- its paran solver can apparently only step forward in time, which would lose events for bodies moving backward in right ascension (read; not run);
- star return codes are ignored;
- planets are topocentric while stars are geocentric;
- its eclipse instant is close to the syzygy rather than greatest eclipse;
- its binary doesn't match its binding source;
- most files carry no licence header.

## 12. Is Astrolog practical as a reference executable?

**Conditionally, and only for some things.** It's a real command-line program, buildable on macOS without X11, with fixed-width text output. In its favour:
- `-L0` gives single-instant paran latitude crossings;
- `-Zd` gives rise, set and culmination times.

Against it:
- it doesn't compute birth-day parans at the birth latitude;
- its eclipse support is only a label on new and full moons;
- `-Zd` interpolates linearly over 30-minute steps by default;
- `-L0`'s interpolation factor is hard-coded;
- it ships the faulty 2026/04/14 data build.

Using it would need, in order:
1. a build;
2. replacing its data files with the pinned build;
3. running `-YQ 0` and fixed formats so it can run unattended;
4. a test battery against this engine before trusting any comparison.

None of that was attempted in Phase 1, which only read its source.

## 13. Proposed fixed-star paran implementation

**The proof of concept exists** (`parans.py`, section 1), checked against ERFA to 7.3 ms. Proposed next steps:
1. **A window helper for the chosen convention,** birth ± 12 h (section 16). Other windows (sunrise to sunrise, the local date) only if a hypothesis needs them.
2. **Circumpolar or never rising,** decided from the body's altitude at its transits. Swiss Ephemeris can't tell the two apart.
3. **Only if a hypothesis needs them,** other definitions than the chosen one (section 16):
   - the zodiacal definition: rising through Swiss Ephemeris's geocentric, latitude-free mode; culmination (the MC on the body's degree) through a solver of the project's own;
   - a geocentric actual-position definition, with the project's own altitude solver.
4. **Star lists** chosen by designation, with magnitudes. Paran gaps stay continuous numbers; any orb is the researcher's rule.
5. **Cost:** 0.01 ms per body and 0.04 ms per star per Swiss Ephemeris call (measured). A day's events for 10 bodies and 50 stars, refined, is on the order of a thousand calls, tens of milliseconds (estimated, not measured).
6. **Tests:**
   - extend the ERFA checks to the window helper, and to any further definition a hypothesis adds;
   - optionally cross-check `-L0` latitudes against Astrolog after its battery (section 12).

## 14. Proposed eclipse implementation

**The proof of concept exists** (`eclipses.py`): nearest solar and lunar eclipses before and after, of the types the caller chooses. Each has:
- greatest eclipse on NASA's definition;
- the exact syzygy;
- type, magnitudes and saros;
- the Sun's and Moon's longitudes, latitudes, right ascensions and declinations at both instants.

Swiss Ephemeris provides the search, classification and attributes. The refinement, the syzygy, the positions at the instant and the date refusals are the project's own.

Proposed next steps:
1. **The eclipse degree is decided** (section 16): the syzygy, with the Sun's position for solar eclipses and the Moon's for lunar ones. A rules layer reads `eclipse.syzygy.sun` or `eclipse.syzygy.moon`.
2. **Local circumstances at the birthplace:** visibility, contacts and magnitude there (`sol_eclipse_when_loc`/`how`).
3. **Investigate the unexplained residuals** against NASA: lunar times, non-central magnitudes and the place of greatest eclipse.
4. **More eclipses,** such as the nearest N on each side, if research needs them.

## 15. Recommended scope for Phase 2

1. **The IERS-based time scale** (section 16): UT1 − UTC from a pinned IERS C04 file, by hash like the ephemeris files, refusing dates past its last day unless another scale is named. It becomes the default.
2. **A result serialisation format** (likely JSON with a schema) that keeps full precision and provenance. Then persistence in PostgreSQL keyed by the calculation key, with subjects, sources, datasets and outcomes as separate tables.
3. **A batch runner:**
   - process-based;
   - recording per-record refusals as failed rows;
   - re-running only what a change of conventions or data invalidates, which the key makes visible.
4. **A review of historical time-zone sources** for the curated charts before 1970, since tzdb isn't authoritative there.
5. **The parans and eclipse next steps** above, as far as the research needs them.
6. **Only then the Django application,** on a stable engine interface.
7. **Optional:**
   - pinning pre-1800 data files;
   - an Astrolog reference battery.

Research rules (orbs, weights, scores) stay outside the engine until the researcher defines them.

## 16. Decisions after review (2026-11-09)

| Question | Decision | Evidence and consequences |
|---|---|---|
| The default time scale | **Required for now. Phase 2 adds a scale taking UT1 − UTC from a pinned IERS file, and makes it the default.** | Measured against IERS EOP 20 C04 (section 5): Swiss Ephemeris's model is within 0.066 s for 1972–2022 but drifts to 0.40 s by late 2026, because its ΔT table is extrapolated after 2023.0. The clock convention is off by up to 0.81 s before 2023. IERS's own values are accurate to about 20 µs. Dates past the pinned file's last day will be refused unless another scale is named. |
| The project's licence | **AGPL-3.0-or-later** | Matches Swiss Ephemeris's AGPL option and pysweph's own grant, with no Professional licence and no binding replacement. All other dependencies are compatible. If the platform is hosted for others, its source must be offered to them. |
| An eclipse's degree and declination | **At the syzygy: the Sun's position for solar eclipses, the Moon's for lunar ones** | Sun and Moon are then exactly aligned in longitude. The engine already returns both instants and both bodies, so nothing in the code changes; greatest eclipse stays the eclipse's instant. |
| The paran window and definition | **Birth ± 12 hours; the body's actual position, topocentric** | The window is symmetric, independent of time zones, and defined at every latitude, unlike sunrise to sunrise. The definition is the one already built and checked against ERFA to 7.3 ms. Phase 2 adds the window as a named helper. |

None of these changed the engine's code, so calculation keys are unchanged.
