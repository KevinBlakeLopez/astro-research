# Dependency assessment

This assessment covers Swiss Ephemeris, its two Python bindings (pysweph and pyswisseph), Immanuel, Morinus Special Edition, Valens, Astrolog and Planetdance. It was carried out between 2026-10-31 and 2026-11-06. Section 9, IERS's UT1 data, was added on 2026-11-09 for the default time scale, and section 10, the research platform's dependencies, the same day.

**How it was done:**
- Sources were fetched at the commits named below and **read, never built or run**.
- Swiss Ephemeris, both bindings and Immanuel were also run on the project machine: macOS 26.6.2 on an Apple M5 Pro (arm64), with CPython 3.13.11.
- No developer was contacted.

**How each claim is marked:**
- **(read)**: seen in source or documentation, cited as file:line at the stated commit.
- **(observed)**: measured at runtime on this machine.
- **(inferred)**: reasoned from what was read.
- **(unverified)**: not confirmed either way.

## Summary

| Project | Audited version | Licence | Recommendation |
|---|---|---|---|
| Swiss Ephemeris | C library 2.10.03 at aloistr/swisseph `5ae0bce`; data at `cae9ecd` | AGPL or Swiss Ephemeris Professional | **Adopt** (direct, through the project's own adapter) |
| pysweph | 2.10.3.6 (`9dd0ca7`) | Mixed: AGPL grant in the wrapper code, Swiss Ephemeris notice in `LICENSE` | **Adopt**, pinned, with the project's own tests |
| pyswisseph | 2.10.3.2 (`778903d`) | AGPL, plus a GPL-2.0+ helper library compiled in | **Reject** as a dependency |
| Immanuel | 1.6.0 (`0f1ef10`) | AGPL-3.0 | **Reference** only |
| Morinus Special Edition | 9.6.4 (`8bb50f3`) | GPL-3.0+ (one file header) | **Reference** for definitions; reject the code |
| Valens | "1.0" (`c1476af`) | Contradictory (MIT vs GPL-3.0+) | **Reference** for definitions; reject as dependency or oracle |
| Astrolog | 8.00 (`5bf172e`, `ast80src.zip`) | GPL-2.0+; bundled Swiss Ephemeris AGPL/Professional | **Reference** executable, possible after a build and a test battery; not for eclipses |
| Planetdance | site changelog to 2026-10-21 | Closed freeware | **Reject** |
| IERS EOP 20 C04 (data) | `eopc04.1962-now`, Last-Modified 2026-11-09 | None found (unverified) | **Adopt**, packaged and pinned by hash |
| Django, psycopg, PostgreSQL (platform only) | Django 6.1.3, psycopg 3.3.8, `postgres:18.6` | BSD-3-Clause; LGPL-3.0-only; PostgreSQL Licence | **Adopt** for the research platform; the engine depends on none of them |

## What each project offers for the Phase 1 requirements

"—" means not provided (read); "not audited" means it wasn't checked. Notes refer to the sections below.

| Requirement | Swiss Ephemeris | Immanuel | Morinus | Valens | Astrolog |
|---|---|---|---|---|---|
| Positions with RA and declination | yes | declination only; RA computed but not returned | yes | yes | yes |
| Ascendant, MC, Vertex | yes (`houses_ex2`) | yes | Asc and MC; Vertex not audited | Asc and MC | yes |
| True and mean node | yes | yes | yes, one at a time | yes, one at a time | yes, one switch for nodes and Lilith together |
| Mean and osculating apogee | yes | yes | — | — | yes, tied to the node switch |
| Raw separations without orbs | — (application level) | — (always through orbs) | not audited | not audited | — (orbs) |
| Octile series, parallels | — (application level) | no parallels; octiles not audited | parallels named in its UI texts; not audited | not audited | yes (semi-square, sesquiquadrate, parallels) |
| Silent fallback detected | only through the return flag (positions) | no | not audited; star return codes ignored | no (check commented out) | not audited; prints Swiss Ephemeris errors once |
| Ambiguous or nonexistent local times refused | — | no (flags ambiguous; accepts nonexistent) | not audited | not audited | not audited |
| UT1 from UTC | `utc_to_jd` | no (UTC as UT) | not audited | not audited | not audited |
| Fixed stars | yes (`fixstar2`) | yes; catalogue may be missing from PyPI | yes | yes, 30 stars | yes, 50 slots |
| Rise, set and meridian transits | yes (`rise_trans`) | not audited | yes | yes | own algorithm |
| Parans | — (application level) | not audited | yes, own convention | — | single-instant latitude crossings |
| Nearest eclipses | search functions only | eclipse searches in its transit tools | ±1 year list | — | annotations only |
| Runs headless | yes | yes | no (`wx` in the chart core) | modules yes, options no | yes (CLI) |
| Native on Apple Silicon | via pysweph | pure Python | no binary found | no arm64 binary | builds from source (unverified on arm64) |
| Test suite | not audited | 226 tests | none | none | none |

## 1. Swiss Ephemeris

| Field | Assessment |
|---|---|
| Project | Swiss Ephemeris, Astrodienst AG. Official site https://www.astro.com/swisseph/; source https://github.com/aloistr/swisseph. |
| Version | C library 2.10.03 as pysweph builds it, from aloistr/swisseph `5ae0bce00dbc66c6315c86da20518e3dd138255b` (2025-12-22) (read). Data files from `cae9ecd4b201544d85e411aced17660932514d43` (2026-05-29): DE441 build 2026/05/26 (read, observed). |
| Language | C. |
| Licence | Dual: GNU AGPL, or the paid Swiss Ephemeris Professional License. The choice must be made before distributing software or activating a public service, and AGPL requires the whole project under AGPL or a compatible licence (`LICENSE`, identical at both commits) (read). |
| Maintenance | Latest commits 2026-10-19, 10-20 and 10-25; 5 open issues (GitHub API, 2026-11-06) (read). |
| macOS | Runs natively on arm64 through pysweph's cp313 wheel (observed). |
| Headless | Yes; it's a library. |
| Relevant features | **Provided directly** (manual §6–8, `swecl.c`, `sweph.c`) (read, observed):<br>• positions in ecliptic and equatorial coordinates, with speeds;<br>• mean and true node, mean, osculating and interpolated apogee;<br>• Ascendant, MC, ARMC and Vertex (`houses_ex2`);<br>• ΔT and sidereal time;<br>• fixed stars (`fixstar2`);<br>• rise, set and meridian transits (`rise_trans`);<br>• global and local solar eclipse searches, lunar eclipse searches and eclipse attributes (`sol_eclipse_when_glob`, `sol_eclipse_where`/`how`, `lun_eclipse_when`/`how`), forward or backward.<br>**Left to the application:** raw separations and aspect deviations; parallels; octiles; civil time that refuses ambiguous input; nearest-eclipse pairs with positions; the syzygy; NASA's definition of greatest eclipse; listing every event in a window; pairing events for parans; telling circumpolar from never rising. |
| Relevant files | `sweph.c` (positions), `swehouse.c` (angles), `swecl.c` (eclipses; `swe_rise_trans_true_hor`; `calc_mer_trans`), `swephlib.c:3460-3481` (sidereal-time model switch at 1850 and 2050), `swephexp.h` (flags). |
| Test coverage | No unit-test suite was audited (unverified). The project validates Swiss Ephemeris independently against JPL Horizons, ERFA and NASA's canon (`CALCULATION_CONVENTIONS.md` §13). |
| Risks | **Silent fallbacks:**<br>• With missing data files, positions fall back to the Moshier model and only the return flag says so (observed).<br>• Eclipse searches and `rise_trans` fall back with no flag at all (observed).<br>• `SE_EPHE_PATH` overrides the configured path (observed).<br>**Data and caching:**<br>• A data build (2026/04/14) returned Mercury up to 104° wrong without warning (observed).<br>• `fixstar2` keeps the first star catalogue it reads for the whole process (observed).<br>• The UTC conversions read extra leap seconds from a `seleapsec.txt` in the data path at their first call, and keep them (`swedate.c:311-344`) (read, observed).<br>**Accuracy limits:**<br>• `rise_trans`'s first answers are off by up to 2.6 s (stars' transits) and 0.69 s (the Moon's) (observed).<br>• Sidereal time jumps by 97 mas and 1.9″ at its model switches (observed).<br>• The Moon file steps at segment boundaries (observed).<br>• Slow bodies' speeds aren't exactly the rate of change of their positions (observed).<br>**API traps:**<br>• Out-of-range coordinates are accepted (observed).<br>• `BIT_FORCE_SLOW_METHOD == FLG_TOPOCTR` (observed).<br>• `rise_trans` sets the topocentric position process-wide (read).<br>**Time:**<br>• The ΔT table is extrapolated after 2023.0 (`swephlib.c:2426-2496`) (read) and 0.40 s off IERS by 2026-10 (observed).<br>• Functions that take UT apply that ΔT internally; `set_delta_t_userdef` overrides it process-wide (observed).<br>• From 2033-09-17, `utc_to_jd` stops applying leap seconds and takes the reading as UT1, so one second of readings across that day is two of TT (observed). `jdet_to_utc` tests the same condition at the instant, so for TTs from 2033-09-16 15:59:27 UTC its reading is a second early (observed).<br>**Licence:** AGPL obligations unless a Professional licence is bought. |
| Reuse | **Adopt**, through one adapter that checks every result (see `CALCULATION_CONVENTIONS.md` §3). |

## 2. pysweph

| Field | Assessment |
|---|---|
| Project | pysweph, a community-maintained successor to pyswisseph. https://github.com/sailorfe/pysweph; PyPI `pysweph`. |
| Version | 2.10.3.6 (PyPI, 2026-02-19), tag `v2.10.3.6` = `9dd0ca73345406addd5597ccb30488391b61cd1a` (read). Its runtime constants still say 2.10.3.4 (`pyswisseph.c:39-40`) (read). |
| Language | C extension (`pyswisseph.c`, 6,441 lines) over a `libswe` submodule pinned to aloistr/swisseph `5ae0bce` (read). |
| Licence | **Mixed signals.** The wrapper's header still grants AGPL v3 or later (`pyswisseph.c:7-10`, `setup.py:10-20`). On 2026-02-19 (`9dd0ca7`) `LICENSE` was replaced with Astrodienst's dual-licence notice, closing issue #8, whose text matches pyswisseph #92 (read). Inferred: a Professional licensee still faces an AGPL grant on the binding itself. |
| Maintenance | One maintainer; releases 2026-01-25 to 2026-02-19; last commit 2026-02-20; open issues #2 (error string from `houses_ex2`) and #9 (Python 3.14) (read). |
| macOS | arm64 wheels for CPython 3.8–3.13; none for 3.14 and none for x86_64 (read). Installed and run on this machine (observed). |
| Headless | Yes. |
| Relevant features | `calc`, `calc_ut` and `deltat_ex` return the error string, which is what exposes a Moshier fallback (`pyswisseph.c:303, 334, 364, 727`) (read, observed). House functions return 13 cusps, index 0 empty (read, observed). It sets a default ephemeris path on import (`pyswisseph.c:43-53`) (read). |
| Relevant files | `pyswisseph.c`, `setup.py`, `LICENSE`, `docs/concepts/migration_guide.md`, `.github/workflows/*`. |
| Test coverage | **Nothing verifies the fork.** Its `tests/` are pyswisseph's and contradict its own return shapes. `README.md:31` declares them deprecated, and CI only builds and imports the module (read). The project's test suite is therefore the only check on the binding's behaviour. |
| Risks | **Shared module name:** it installs `swisseph`, as pyswisseph does, and the two silently overwrite each other; `pip check` passes (observed). The project's dependencies exclude pyswisseph (`pyswisseph<0`).<br>**Single maintainer,** and no commits for over eight months.<br>**No tests** of its own.<br>**Licence signals mixed.** |
| Reuse | **Adopt**, pinned to 2.10.3.6 with its binary checked against its install record at start-up. Exit plan: a minimal binding of the project's own over a pinned Swiss Ephemeris commit, if a non-AGPL deployment, Swiss Ephemeris 3.0 or a missing wheel requires it (`PHASE1_REPORT.md`, architecture). |

## 3. pyswisseph

| Field | Assessment |
|---|---|
| Project | pyswisseph, Stanislas Marquis. https://github.com/astrorigin/pyswisseph; PyPI `pyswisseph`. |
| Version | 2.10.3.2 (PyPI, 2023-06-04), tag `v2.10.03.2` = `778903d59bed84b8da020cee77f1995b0df5106b`; HEAD `91ec656` (2026-04-01, documentation only) (read). |
| Language | C extension plus bundled `libswe` (astrorigin's copy at `696bda4`, 2022-08-28), `swephelp` and sqlite3 (read). It reports 2.10.03 like pysweph, but 15 shared C/H files differ, so the version string doesn't identify the code (read). |
| Licence | AGPL (`pyswisseph.c:1-19`, `LICENSE.txt`). `swephelp` is GPL-2.0+ and compiled in by default (`setup.py:81`) (read). Issue #92, "License should be compatible with Swisseph dual license", was opened by the Swiss Ephemeris author on 2026-02-17 and is still open with no maintainer reply (read). |
| Maintenance | Last code commit and release 2023-06-04; 23 open issues (read). |
| macOS | Wheels for CPython 3.8–3.11 only (universal2) (read). No arm64 wheel for 3.13; it builds from source with Apple clang (observed). |
| Headless | Yes. |
| Relevant features | `calc_ut` returns `(xx, retflag)` with no error string (`pyswisseph.c:349`) (read, observed); 12 house cusps (read, observed). |
| Test coverage | 126 value tests that need the data files; CI doesn't run them (read). |
| Risks | Dormant; no wheels for current Python on macOS; drops warnings; GPL code compiled in; same module name as pysweph (observed). |
| Reuse | **Reject** as a dependency. |

## 4. Immanuel

| Field | Assessment |
|---|---|
| Project | Immanuel, The Rift Lab. https://github.com/theriftlab/immanuel-python; PyPI `immanuel`. |
| Version | 1.6.0 (PyPI, 2026-09-07), tag `v1.6.0` = `0f1ef10787a2b0fe904bb21d5521287f16571d03`. Master HEAD `e46404f` (2026-10-11) is 13 commits further on and unreleased (read). |
| Language | Pure Python, 3.10 or later; depends on pysweph, python-dateutil, scipy and timezonefinder ~5.2 (read). |
| Licence | AGPL-3.0 (`LICENSE.md`, `README.md:228-232`), with no per-file grants (read). It bundles Swiss Ephemeris data files without Astrodienst's licence text (read). |
| Maintenance | Active: four releases between 2025-09 and 2026-09, last commit 2026-10-11, no open issues, one main author, no CI configuration (read). |
| macOS | Pure Python; limited in practice by pysweph's wheels (inferred). Installed and run on this machine (observed). |
| Headless | Yes (read, observed). |
| Relevant features | **What it offers (read):**<br>• declinations, by converting ecliptic coordinates with the true obliquity (`tools/sweph.py:74…`);<br>• mean and true node; mean, osculating and interpolated apogee (`sweph.py:55-62`);<br>• the Vertex;<br>• configurable aspects and orbs (`settings.py:191-262`);<br>• JSON that keeps raw floats;<br>• natal, return, progressed, composite and transit charts; tropical and geocentric only.<br>**Where it doesn't fit (read unless marked):**<br>• It passes no ephemeris flags and keeps only element `[0]` of each result, so a Moshier fallback goes unnoticed (`sweph.py:73, 94, 110, 135, 182, 268-290, 377, 382`).<br>• Right ascension is computed but not returned, and there are no parallels (`sweph.py:74-85`).<br>• Every aspect goes through an orb (`reports/aspect.py:65, 94`).<br>• UTC is used as UT (`tools/date.py:100, 109`; observed).<br>• An ambiguous local time takes its first occurrence without an error, but an `ambiguous` flag is set (`date.py:122-125`, `support/subject.py:42-44`).<br>• Nonexistent local times are accepted (read, observed).<br>• Times are snapped to the whole second within ±0.05 s (`date.py:36, 50-60`). |
| Relevant files | `immanuel/tools/sweph.py`, `tools/date.py`, `tools/ephemeris.py`, `reports/aspect.py`, `settings.py`, `support/subject.py`, `support/wrap.py`, `MANIFEST.in`. |
| Test coverage | 226 test functions. One chart is compared with astro.com to the arcsecond, as strings; there are date tests for one ambiguous time and for time snapping (read). Not tested: return flags or fallbacks, out-of-range dates, polar latitudes, nonexistent times, aspects across 0°/360°, packaging (read). |
| Risks | **Calculation:**<br>• Placidus, the default house system, raises at polar latitudes (inferred).<br>• Asking for the Vertex without a house system calls Swiss Ephemeris with the number that means Venus (`tools/ephemeris.py:435-459`) (inferred).<br>• A time zone already attached to an input is replaced rather than converted (`date.py:43-49`; reproduced with the standard library).<br>**Packaging:**<br>• `sefstars.txt` is missing from the 1.6.0 sdist (`MANIFEST.in:1`), so fixed stars may fail from PyPI (inferred; wheel not inspected).<br>• Its `pysweph>=2.10.3.3` bound is too loose for its 13-cusp indexing (read).<br>**Global state:** importing it changes Swiss Ephemeris's process-wide data path (`immanuel/__init__.py:3`) (read).<br>**Licence:** AGPL for the whole package. |
| Reuse | **Reference.** Its aspect and orb configuration is a model for a later rules layer. Its astro.com values are consistency checks only, because they're Swiss Ephemeris-derived and so not independent validation. Not adopted as the chart layer (`PHASE1_REPORT.md`, section 10). |

## 5. Morinus Special Edition

| Field | Assessment |
|---|---|
| Project | Morinus Special Edition ("morinus-updated"), maintainer shin10567. https://sourceforge.net/projects/morinus-updated/; git `https://git.code.sf.net/p/morinus-updated/code`. |
| Version | HEAD `8bb50f3386226746bf912ae967789cc10cf4499a` (2026-09-05); About box 9.6.4 (`morin.py:4236`); project page 9.6.3 (read). |
| Language | Python with a wxPython GUI, and its own C binding `sweastrology` (`SWEP/src/astrologymodule.c`) (read). |
| Licence | GPL-3.0-or-later, but the header appears only in `morinus.py:5-19`; `Res/COPYING.txt` is GPL v3 (read). The paran, eclipse, rise/set and fixed-star files carry no header (read). The bundled Swiss Ephemeris source is GPL-2.0+ or Professional, and its data folder AGPL or Professional (read). |
| Maintenance | Active: 173 commits since an import of 8.1.0+ on 2025-09-03 (read). |
| macOS | The shipped binary is a 32-bit Windows `.pyd` containing "2.09.01"; the older binaries are Python 2.7 builds (read). No macOS build was found (inferred). The bundled C source is Swiss Ephemeris 1.76.00 and doesn't match the shipped binary's interface (read). |
| Headless | **No.** `chart.py` imports `options.py`, which imports `wx` (`options.py:4`) (read). |
| Relevant features | **Parans** (`paranwnd.py:948-1024`) (read):<br>• its own solver, not `rise_trans`: target sidereal times from semi-diurnal arcs, body centre on a geometric horizon, no refraction;<br>• Sun to Saturn paired with the user's stars, all 16 angle pairings;<br>• window from sunrise to sunrise (±12 h without a sunrise);<br>• fixed orb of ±2 minutes;<br>• planets topocentric, stars geocentric.<br>**Rise/set** (`riseset.py`): `rise_trans` with disc centre and no refraction (read).<br>**Eclipses** (`eclipses.py`) (read; the instant is the auditor's derivation):<br>• birth ±1 year;<br>• lunar via `lun_eclipse_when`;<br>• solar by scanning new moons with `sol_eclipse_where`, because the global search is avoided (a code comment says it crashes);<br>• the reported instant is close to the syzygy, not greatest eclipse;<br>• the Sun's or Moon's geocentric declination is shown.<br>**Nodes and apogees:**<br>• true or mean node (`planets.py:834-836`) (read);<br>• no use of any apogee point found (read).<br>**Rectification** controls exist in the primary-direction windows (`primdirslistframe.py` and others) (read). |
| Relevant files | `paranwnd.py`, `riseset.py`, `eclipses.py`, `eclipseswnd.py`, `fixstars.py`, `planets.py`, `chart.py`, `options.py`, `SWEP/src/astrologymodule.c`. |
| Test coverage | None found (read). |
| Risks | **Calculation:**<br>• The paran solver can only step forward in time; in the auditor's toy model this lost almost every event for bodies whose right ascension drifts backward (inferred; not run in Morinus).<br>• Star-call return codes are ignored, so an unknown star would sit at RA 0, Dec 0 (inferred).<br>• Events that don't occur are printed as "00:00:00" (read).<br>**Build and licence:**<br>• The binding source doesn't match the shipped binary (read).<br>• Windows-only binaries.<br>• Licence headers are missing from most files. |
| Reuse | **Reference** for definitions: its paran convention and its "eclipse declination" usage. **Reject** the code. |

## 6. Valens

| Field | Assessment |
|---|---|
| Project | Valens, Robert Nagy (MrBig321). https://github.com/MrBig321/Valens. |
| Version | HEAD `c1476af52812a56a693f1d7122e231886e8324dc` (2025-04-14, an empty commit). All 87 `.py` files come from the 2024-11-09 upload; the code is 2014-era. The About text says "Valens 1.0" (`texts.py:207`). There are no tags or releases (read). |
| Language | Python 3 with Tkinter. Swiss Ephemeris is reached through a compiled extension, `sweastronomy`, available only as binaries (`Web/ExtensionMod.zip`, Swiss Ephemeris 1.76.00, a 2009 release) (read). |
| Licence | **Contradictory.** `LICENSE.md`, the README and the GitHub API say MIT; `valens.py:4-18` and the About text say GPL-3.0-or-later (read). The bundled Swiss Ephemeris binaries come without Swiss Ephemeris licence text or wrapper source (read). |
| Maintenance | Effectively an archive: 10 commits, last pushed 2025-04-14, no issues (read). |
| macOS | The extension is i386 + x86_64 with no arm64 slice (read), so it can't load in native Python on Apple Silicon (inferred). The author never tested on a Mac (`Docs/installation.txt:117-119`) (read). |
| Headless | The nine named calculation modules import no GUI toolkit. But `Chart` needs an options object whose module imports `tkinter.messagebox`, and only the GUI sets the ephemeris path (`vframe.py:259-261`) (read). |
| Relevant features | All nine files named in the brief exist (read):<br>• `astronomy.py` holds only constants; the extension does the work;<br>• positions and RA/Dec come from two `swe_calc_ut` calls (`planet.py:23-25`), with the error check commented out (`planet.py:34-35`);<br>• its own notes tell users not to install the data files (`Docs/remarks.txt:9`), so positions are Moshier in practice (inferred);<br>• houses W/O/B/R/P;<br>• mean or true node, and no apogee;<br>• 30 fixed stars from the old `fixstars.cat`;<br>• `rise_trans` for the seven classical planets, with return codes discarded;<br>• **syzygy** (`syzygy.py:44-173`): the last new or full moon before birth, found by brute force in steps of 1 h, then 1 min, then 1 s;<br>• no eclipses or parans. |
| Relevant files | `planet.py`, `planets.py`, `houses.py`, `fixedstars.py`, `riseset.py`, `syzygy.py`, `placspec.py`, `chtime.py`, `vframe.py`, `options.py`, `Docs/remarks.txt`. |
| Test coverage | None (read). |
| Risks | **Swiss Ephemeris layer:**<br>• Binary-only Swiss Ephemeris 1.76, which had a known fixed-star proper-motion bug fixed in 1.77 (read; it would apply to Valens's stars, inferred).<br>• Return codes ignored throughout (read).<br>**Possible bugs** (source reading):<br>• the equation-of-time sign (`chtime.py:81-85`);<br>• the retrograde test, which checks distance instead of speed (`transits.py:301-304`);<br>• longitude seconds dropped (`vframe.py:408`).<br>**Security:** pickle files execute code when loaded.<br>**Licence:** contradictory. |
| Reuse | **Reference** for definitions: the pre-natal syzygy rule, the Placidian speculum and primary directions. **Reject** as a dependency or test oracle. |

## 7. Astrolog

| Field | Assessment |
|---|---|
| Project | Astrolog, Walter D. Pullen. https://www.astrolog.org/astrolog.htm; source `https://www.astrolog.org/ftp/ast80src.zip`; mirror github.com/CruiserOne/Astrolog. |
| Version | 8.00 (`astrolog.h:494-496`). `ast80src.zip` (Last-Modified 2026-05-31, SHA-256 `8c0c33eac2c40d158c007f3a4168516972c9fc78830f65eba38ac2be46541d63`) is identical, apart from a README, to tag `v8.00` = `5bf172ea231c4b6ea3d7e09ca307571354a41e8a` (read). |
| Language | C++. |
| Licence | GPL-2.0-or-later, with notices for PLACALC (Astrodienst), the geonames atlas (CC BY 4.0) and PostScript code (`astrolog.h:3-46`). The bundled Swiss Ephemeris files are AGPL or Professional (read). Building and running it privately as a reference distributes nothing (inferred; not legal advice). |
| Maintenance | Release May 2026; last code change 2026-05-28 (`astrolog.h:51`) (read). |
| macOS | The Makefile uses `cc`. X11 is optional: comment out `#define X11` (`astrolog.h:69`) and drop `-lX11` (read). There's no official Mac binary and no mention of arm64 (read). It was not built here (unverified). |
| Headless | Yes, a command-line program that prints text to stdout (`astrolog.cpp:3407-3450`) (read). Automation pitfalls (read):<br>• it prompts when given no chart data;<br>• it pages output every 24 lines even when piped, unless given `-YQ 0`;<br>• it uses 12-hour times and minute precision by default;<br>• it can fetch JPL web queries through `wget` (turn off with `-0n`);<br>• `-sr`/`-sr0` alter `-Zd` and `-L` output within the same run. |
| Relevant features | **Positions and declinations:** `-v`, with `=sr0` for declinations (read).<br>**Nodes and Lilith:** one switch picks mean node with mean Lilith, or true node with osculating Lilith (`-Yn`, `astrolog.cpp:544`, `calc.cpp:2590-2595`); `-Ynn` uses the interpolated apogee (read).<br>**Aspects:** 24 kinds, including semi-square and sesquiquadrate, plus parallel and contra-parallel (`data.cpp:332-341`), applied with orbs (read).<br>**Parans:** `-L0` gives astro-map latitude crossings for a single instant: in mundo, disc centre, geometric horizon, no orb. Its interpolation factor is hard-coded to 5.0 even when the band step changes (`charts1.cpp:2158, 2201`), a likely bug (read).<br>**Rise and set:** `-Zd` uses its own algorithm, with 48 linear-interpolation steps a day by default; one event per body per step; at most 300 events a day, truncated silently (read).<br>**Eclipses:** only a label on new and full moons in `-d` listings, from its own geometry (read).<br>**Stars:** `swe_fixstar2`, return value unchecked (read). |
| Relevant files | `calc.cpp`, `charts1.cpp` (`-L`), `charts3.cpp` (`-Zd`), `charts2.cpp` (eclipse annotations), `data.cpp`, `astrolog.cpp`, `astrolog.as`, `Makefile`. |
| Test coverage | No test files in the source distribution (read). |
| Risks | **Bundled data:** the 2026/04/14 build (SHA-256 `20aa1c1d…`), whose Mercury is wrong by up to 104° in early 1800 (observed for that build). The files must be replaced before use as a reference.<br>**Output:** text meant for people, with no machine-readable format.<br>**Precision:** the coarse default time steps limit it.<br>**Not run:** no behaviour was verified at runtime. |
| Reuse | **Reference executable, after a build and a test battery** against the project's own results: `-L0` with other steps, `-Yf` refraction, culminations near the zenith, the 300-event cap. Then it's useful for spot-checks of single-instant paran latitudes and of rise and culmination times at `:d 2880`. **Not** useful for eclipses. |

## 8. Planetdance

| Field | Assessment |
|---|---|
| Project | Planetdance, Jean Cremers. https://www.jcremers.com/. |
| Version | No version number published; the changelog runs to 2026-10-21 (`whatsnew.txt`) (read). |
| Language, licence | Closed-source Windows freeware with a donation page; no licence text found (read, inferred). |
| macOS, headless | Windows installers only; the changelog mentions fixes "on wine / linux / mac" (read), so no native macOS build (inferred). No documented command-line mode; it has an in-app scripting language, AstroBasic (read). |
| Relevant features | Built on Swiss Ephemeris 2.10.03 since 2022-08-28 (changelog) (read). |
| Reuse | **Reject.** It's Windows-only with no command line, and its calculations add nothing over Swiss Ephemeris itself. The brief rules out Windows virtualisation. |

## 9. IERS EOP 20 C04 (data)

| Field | Assessment |
|---|---|
| Project | The IERS Earth Orientation Parameters Product Centre, Paris Observatory. Series and readme at https://hpiers.obspm.fr/iers/eop/eopc04/. |
| Version | `eopc04.1962-now`, Last-Modified 2026-11-09 14:21:06 GMT, SHA-256 `5e98a6a8…60fb90`, daily from 1962-01-01 to 2026-10-10 (read). The readme names the series C04 2020, version 4, started 2026-02-05 (read). |
| Licence | No licence or terms of use in the file or its readme (read); not checked further (unverified). |
| Use | UT1 − UTC for `TimeScale.UT1_FROM_IERS`, 1972-01-01 to 2026-10-10, copied into `src/astro_research/iers_eop20c04_ut1.txt` (`CALCULATION_CONVENTIONS.md` §2.2). |
| Risks | • The series ends about a month before its release date, and the final combination stops 30 days before it (readme) (read).<br>• Past values change between versions: the readme lists four versions of the 2020 series (read).<br>• A new table changes every calculation key, since its hash is part of the provenance (observed). |
| Reuse | **Adopt**: packaged, pinned by size and SHA-256, and checked when the ephemeris is opened. |

## 10. The research platform's dependencies

The platform (`research-platform/`, `docs/RESEARCH_PLATFORM.md`) is a separate distribution; the engine depends on none of these (`tests/test_boundary.py`). Versions, dates and the packages' licences are from PyPI's, npm's and Docker Hub's metadata on 2026-11-09 (read). Docker Hub gives no licence: PostgreSQL's is from its own documentation, and the image also holds Debian's packages (unverified).

| Package | Pinned | Released | Licence | Use |
|---|---|---|---|---|
| Django | 6.1.3 | 2026-11-03 | BSD-3-Clause | Models, migrations, the `compute_dataset` command |
| psycopg (with psycopg-binary) | 3.3.8 | 2026-11-04 | LGPL-3.0-only | Django's PostgreSQL driver |
| PostgreSQL image | `postgres:18.6`, digest `sha256:e3d24f69…df3c42` | 2026-11-06 | PostgreSQL Licence | The tests' and local development's database (`research-platform/compose.yaml`) |
| django-stubs | 6.1.2 (development) | 2026-10-05 | MIT | Types for mypy's strict check |
| django-stubs-ext | 6.1.2 | 2026-10-05 | MIT | Lets classes written for the type checker, such as `admin.ModelAdmin[Record]`, run (`monkeypatch()` in the settings) |
| HTMX | 2.0.11, `htmx.min.js`, SHA-256 `d6fdc75f…00f717` | 2026-09-22 | 0BSD | The chart page's pairs table; served from the platform's static files |
| pytest-django | 4.14.0 (development) | 2026-08-10 | BSD | The platform's tests |

- **Recent releases.** Django's and psycopg's pins were less than a week old when chosen; they are patch releases, which usually carry security fixes.
- **HTMX** came from the npm package `htmx.org-2.0.11.tgz`, whose SHA-512 matched the registry's integrity value before the file was taken out (observed). npm also tags a 4.x as `next`; 2.0.11 is its latest stable release.
- **Licences.** LGPL-3.0 permits use of the library from AGPL-3.0-or-later code (inferred from the licence texts; not legal advice). The others are permissive.
- **psycopg-binary** bundles its own libpq, as psycopg's documentation describes, so the Homebrew libpq on this machine isn't used (unverified).
- **Not audited:** none of these was read beyond its metadata and documentation, and the PostgreSQL image's contents weren't inspected.

## What was not done

- Morinus, Valens and Astrolog were not built or run. Their behaviour is from source reading only.
- No wheel contents were inspected. PyPI's inspector requires a browser check that wasn't passed.
- Morinus's and Valens's aspects, and the time handling of Morinus, Valens and Astrolog, were not audited in detail.
- Swiss Ephemeris's own test assets were not audited.
