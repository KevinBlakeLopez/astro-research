# Calculation conventions

This document defines every convention the Phase 1 engine (`astro_research`, version 0.1.0) uses. For each one it says what is computed, which alternative is not used, and where the choice is recorded.

**How choices are recorded.** Each module records its conventions as a versioned string, for example `positions-v2: …`; [the appendix](#appendix-convention-strings) quotes them all. Those strings travel with every result, and they're part of every chart's calculation key. A change in convention therefore changes both the string and the key.

**Choices the engine doesn't make.** The engine never picks a research choice on the caller's behalf. Wherever the alternatives are equally defensible, the caller must name one, with no default:
- the time scale;
- the aspect angles;
- time offsets for a sensitivity sweep;
- eclipse types;
- the horizon convention for rise and set;
- the paran window.

Numbers are as measured during Phase 1. Some are bounds the test suite enforces; the others are one-off measurements, described in `PHASE1_REPORT.md`.

## 1. Summary

| Topic | Convention | Not used |
|---|---|---|
| Zodiac | **Tropical**: longitudes are measured from the true equinox of date. | Sidereal zodiacs (no ayanamsa is ever applied). |
| Geocentric or topocentric | **Geocentric** for positions, angles, measurements and eclipses. **Topocentric** only for rise, set and transit events (parans), as Swiss Ephemeris computes them. | Topocentric chart positions. |
| Apparent or true | **Apparent**: light-time, annual aberration, light deflection by the Sun, and nutation are applied. | Geometric or astrometric positions as results. They're used only inside tests as reference frames. |
| Ecliptic or equatorial | **Both**: ecliptic longitude and latitude on the true ecliptic of date, and right ascension and declination on the true equator of date, each with speeds. | J2000 or ICRS frames as results. |
| Lunar nodes | **Both**, as separate points: the mean node and the true (osculating) node. | — |
| Lunar apogees | **Both**, as separate points: the mean apogee and the osculating apogee. | Swiss Ephemeris's interpolated ("natural") apogee and perigee. |
| House systems | **None.** Only the angles are computed: Ascendant, MC, Descendant, IC and Vertex, plus the ARMC. | House cusps of any system. |
| Ephemeris and flags | Swiss Ephemeris with the pinned DE441 files (`FLG_SWIEPH`), plus `FLG_SPEED` and, for right ascension and declination, `FLG_EQUATORIAL`. Every return flag is checked. | Moshier, JPL files, `FLG_TOPOCTR` for positions, `FLG_SIDEREAL`, `FLG_TRUEPOS`/`NOABERR`/`NOGDEFL`/`NONUT` in results. |
| Julian dates | Gregorian calendar only. UT1 Julian dates for the Earth's rotation, TT Julian dates for positions. | Julian-calendar input; dates before 1582-10-15. |
| ΔT | TT = UT1 + Swiss Ephemeris's ΔT for the loaded DE441 files: `deltat_ex(jd_ut1, FLG_SWIEPH)`. | The ΔT that `utc_to_jd` applies internally (DE431-based; 0.30 s different at 1800, 0.04 s at 1900). |
| Units | Angles in degrees, distances in AU, speeds per day, times in seconds or Julian days. Longitudes are east-positive and elevations in metres. | — |
| Numerical precision | Swiss Ephemeris's doubles, unrounded. Julian dates resolve to about 40 µs, and UTC readings to the microsecond. | Rounding of any measurement. |

## 2. Time

### 2.1 Civil input (`civil_time.py`)

A birth time comes in one of four forms, each its own input type:

| Input | Meaning | Offset source |
|---|---|---|
| `UtcTime(utc)` | A UTC instant. Any tzinfo with a zero offset is accepted. | Given. |
| `ZonedLocalTime(local, zone, stated_utc_offset=None, accept_pre_1970_tzdb=False)` | A wall-clock reading and an IANA zone. | tzdb rules, from the pinned `tzdata` package (2026.6), never the operating system's database. The version is recorded in each result that uses it. |
| `OffsetLocalTime(local, utc_offset)` | A wall-clock reading and the offset the source states. | Stated. |
| `LocalMeanTime(local)` | Local mean time at the birthplace. | The birthplace's longitude × 240 s per degree. |

`resolve()` never guesses. Each case below raises a `CivilTimeError` subclass that carries what's needed to correct the record:
- a repeated wall-clock time (`AmbiguousLocalTime`, listing the candidates), unless the stated offset picks one;
- a skipped wall-clock time (`NonexistentLocalTime`, with the offsets before and after);
- a stated offset that contradicts the zone rules (`StatedOffsetMismatch`);
- tzdb's local mean time, which belongs to the zone's reference city, not the birthplace (`TzdbLocalMeanTime`);
- tzdb rules before 1970 (`Pre1970TzdbNotAccepted`), unless the caller accepts them. Before 1970 tzdb is not authoritative, and some zones are links that replace a country's own history; for example, Europe/Amsterdam is a link to Europe/Brussels.

Other inputs refused:
- wall-clock readings carrying a tzinfo, or with `fold=1`;
- dates before the Gregorian calendar (1582-10-15);
- offsets of 24 h or more.

Ambiguity is detected by resolving both folds and checking the round trip. Different offsets with an unchanged round trip mean the time is ambiguous; a changed round trip means it doesn't exist.

### 2.2 Time scales (`astronomical_time.py`, `TimeScale`)

How a UTC reading becomes UT1 is a convention. The caller must name one in every chart: there's no default yet. Phase 2 will add a third scale that takes UT1 − UTC from a pinned IERS data file, and make that the default.

| `TimeScale` | UT1 | Used by |
|---|---|---|
| `CLOCK_AS_UT1` | The UTC clock reading itself (`julday`). | Immanuel does this (observed); other software was not checked. |
| `UT1_FROM_DELTA_T` | Swiss Ephemeris's `utc_to_jd`: UTC to TT through leap seconds, then UT1 = TT − ΔT(model). | — |

The two scales compare as follows:
- **Before 1972** they give identical results, because Swiss Ephemeris treats earlier civil time as UT.
- **After 1972** they differ by UT1 − UTC (at most 0.9 s). That moves the Ascendant by up to about 30″, roughly a third of a one-second birth-time difference.
- **Under either scale**, TT = UT1 + `deltat_ex(UT1, FLG_SWIEPH)`, so positions are always consistent with the loaded DE441 files. Each `AstronomicalTime` records `jd_ut1`, `jd_tt`, `delta_t_s`, `ut1_minus_utc_s` and its time scale.
- **Within a pair of charts** the choice almost cancels: it changes a one-minute Ascendant change by 0.025″ (1987). The exception is a pair that straddles a leap second. Under `CLOCK_AS_UT1` that second is lost in both TT and the angles: 60 s instead of 61, and the Ascendant's change 10.6″ smaller (2016-12-31).

**Accuracy against measured UT1.** Measured against IERS's EOP 20 C04 series, daily from 1972-01-01 to 2026-10-10:

| Births | `UT1_FROM_DELTA_T` error | `CLOCK_AS_UT1` error |
|---|---|---|
| 1972–2022 | ≤ 0.066 s (mean about 0.015 s) | up to 0.81 s |
| 2023 | ≤ 0.085 s | ≤ 0.046 s |
| 2024–2026 | 0.15–0.40 s, growing | ≤ 0.095 s |

Swiss Ephemeris 2.10.03's ΔT table holds measured values up to 2023.0 and extrapolated ones for 2024–2028 (`swephlib.c:2426-2496`), and the Earth's rotation didn't follow the extrapolation. One second of time moves the Ascendant by about 33″ at London.

`Ephemeris.time_from_tt` and `time_from_ut1` turn an event's Julian date back into an `AstronomicalTime`, by way of its UTC reading under the caller's time scale.
- The reading is rounded to the microsecond.
- Under `UT1_FROM_DELTA_T`, an instant inside a leap second has no reading and is refused (`InstantInLeapSecond`).

### 2.3 Julian dates and resolution

- Julian dates near 2.4 million resolve to 2⁻³¹ day, about 40 µs. Differences between two Julian dates carry a few of those roundings, and tests bound them as such.
- Time differences between charts are reported two ways: `utc_seconds`, which is civil time without leap seconds and exact to the microsecond, and `tt_seconds`, which is elapsed time from the two TT Julian dates.

## 3. Ephemeris and environment (`ephemeris.py`)

**Data.** The engine uses three pinned files from aloistr/swisseph at commit `cae9ecd4b201544d85e411aced17660932514d43`, listed in `src/astro_research/ephemeris_manifest.toml` and checked by SHA-256:

| File | Content | Coverage |
|---|---|---|
| `sepl_18.se1` | Planets; DE441 build 2026/05/26 | 1800-01-01 to 2400-01-10 TT |
| `semo_18.se1` | Moon; same build | 1799-12-23 to 2400-01-15 TT |
| `sefstars.txt` | Fixed stars brighter than magnitude 5: 1,360 records, some stars listed under more than one name | — |

- **Pinned by hash, not name.** The earlier 2026/04/14 build returns Mercury up to 104° wrong in early 1800, up to 1800-03-21 (sampled daily from 1800-01-05), with no warning.
- **Dates outside the files are refused** with `DateOutsideEphemeris`. Swiss Ephemeris would otherwise switch silently to its Moshier model, and that applies to positions, eclipses and rise/set alike.

**Binding.** pysweph 2.10.3.6, which bundles Swiss Ephemeris 2.10.03. pyswisseph must never be installed alongside it: the two install the same `swisseph` module and silently overwrite each other. The project's dependency constraints (`pyswisseph<0` in `pyproject.toml`) exclude it.

**The engine fails closed.** `Ephemeris.open()` runs `verify_ephemeris()`, and nothing calculates without the handle it returns. The check confirms:
1. the `swisseph` module is the pinned pysweph build, byte for byte against its install record;
2. the data folder holds exactly the manifest's files, with matching sizes and hashes (dotfiles are ignored);
3. Swiss Ephemeris really loaded those files: the return flag says so, each loaded file lies in the folder, and the DE number matches;
4. the star catalogue Swiss Ephemeris holds in memory is the pinned one.

Point 4 is needed because Swiss Ephemeris keeps the first catalogue it reads for the rest of the process; neither changing the path nor `close` reloads it. The check compares Regulus's barycentric ICRS position at J2000 (with light-time, aberration, deflection and nutation off) with the pinned file's record. It catches a different catalogue only if that catalogue's Regulus differs.

After the check:
- a handle refuses to calculate once the process-wide data path has moved (`StaleEphemeris`), or while `SE_EPHE_PATH` is set, because that overrides the path;
- every position calculation checks Swiss Ephemeris's return flag: no `FLG_SWIEPH` bit raises `DateOutsideEphemeris`, and a non-empty error string raises `EphemerisNotLoaded`;
- eclipse searches and rise/set return no such flag, so they're guarded by refusing any instant or window outside the files.

The adapter is the only module that imports `swisseph`. Every other module works on its results.

**Swiss Ephemeris accepts out-of-range coordinates without complaint** (a latitude of 95° or a longitude of 32,768° both return results). So the engine validates places itself in `GeoPosition`:
- latitude must lie within [−90°, 90°] and longitude within [−180°, 180°], east-positive;
- elevation is in metres;
- every value must be finite.

## 4. Positions (`positions.py`)

`Ephemeris.positions(time, points)` returns, for the ten bodies (Sun, Moon, Mercury to Pluto) and the four lunar points:
- longitude and latitude on the true ecliptic and equinox of date;
- right ascension and declination on the true equator and equinox of date;
- distance in AU;
- a speed for each, per day.

- **Apparent and geocentric** (§1).
- **Jupiter to Pluto are their system barycenters,** as JPL's DE and Swiss Ephemeris give them. Pluto matches JPL Horizons' barycenter to 0.6 mas; its body centre is 76 mas away.
- **Swiss Ephemeris's speeds are its own,** and for slow bodies they aren't exactly the rate of change of its positions. The gap reaches up to 3.9 × 10⁻⁶ °/day for Pluto, about 0.014″ a day, so a slow body's rates (and the rates of separations involving it) carry that error.

## 5. Lunar points

| Point | Definition |
|---|---|
| Mean node | Moshier's adjustment of ELP2000-85 with DE431 corrections (Swiss Ephemeris manual §2.2.1). Expressed in the true equinox of date, so nutation in longitude is included. |
| True node | The osculating node of the Moon's geocentric orbit on the ecliptic of date, from its position and velocity with GM(Earth + Moon). |
| Mean apogee | The same lunar theory as the mean node. It lies on the inclined mean orbit, so it has an ecliptic latitude. |
| Osculating apogee | The apogee of the osculating orbit, with GM(Earth + Moon). It swings about ±30° around the mean apogee. |

- **Steps in the true node and osculating apogee.** At the 27.55-day segment boundaries of the compressed Moon file they jump by up to about 0.085″ and 0.9″ respectively; the Moon itself shows no step.
- **`distance_au`.** For lunar points this is Swiss Ephemeris's orbital distance, and it has no research meaning.

## 6. Angles (`angles.py`)

`Ephemeris.angles(time, place)` returns:
- the ARMC (local apparent sidereal time, in degrees);
- the Ascendant, MC and Vertex, each with its speed;
- the Descendant and IC, as Ascendant and MC + 180°;
- the true obliquity of date;
- a beyond-the-polar-circle flag.

- **Equal houses only.** Angles come from Swiss Ephemeris's `houses_ex2` with Equal houses, which never fail at high latitude. The angles don't depend on the house system; Placidus and Koch raise beyond the polar circles, so they're avoided.
- **Computed from UT1.** Under `CLOCK_AS_UT1`, the clock reading stands in for it.
- **Sidereal time** is Swiss Ephemeris's own:
  - IERS Conventions 2010 strictly between 1850-01-01 and 2050-01-01 UT1;
  - its long-term model at those dates and outside them, including both boundary instants;
  - the angles step by 97 mas at 1850-01-01 and 1.9″ at 2050-01-01;
  - inside the window, the ARMC agrees with ERFA's IAU 2006/2000A apparent sidereal time within 1 mas.
- **The Ascendant** is always the eastern rising point of the ecliptic. Beyond the polar circles the MC stays on the upper meridian even when it's below the horizon.
- **The Vertex** is the ecliptic point due west, always in the western hemisphere. On the equator it's an equinox point (0° Aries or Libra) and doesn't move.
- **Poles.** The angles are undefined at the geographic poles and refused there (`AnglesUndefined`).

## 7. Measurements (`measurements.py`)

Only continuous numbers. There are no orbs, aspect flags, applying or separating labels, or scores.

- **Chart points:** the requested bodies and lunar points, then the Ascendant, MC and Vertex. The Descendant and IC are left out, because a contact with them is a contact with the Ascendant or MC at the opposite angle; they'd only add correlated measurements.
- **Angles' declinations:** asin(sin λ · sin ε), using the true obliquity ε. Their rates follow by the chain rule, holding ε fixed (it changes by about 0.5″ a year).
- **For each unordered pair (a, b):**
  - the signed longitude difference b − a, in (−180°, 180°];
  - the separation, its absolute value, and the separation's rate;
  - the great-circle separation, latitude included;
  - for each requested aspect angle, the deviation (separation − angle; positive means wider than exact) and its rate;
  - the declination difference b − a, which is zero at an exact parallel, and the declination sum, which is zero at an exact contra-parallel, each with its rate.
- **Rates** are in degrees per day and negative while closing. At an exact conjunction or opposition the separation has a corner, and the rate takes whatever sign the formula gives there.
- **Aspect angles** must lie within 0–180° with no repeats. `MAJOR` (0, 60, 90, 120, 180) and `OCTILE` (45, 90, 135, 180) are named sets; they share 90° and 180°, so combine them as a sorted union.

## 8. Charts and calculation keys (`chart.py`)

**`BirthRecord`** holds:
- a civil time and a place;
- optionally, the time's recorded resolution (hour, minute or second);
- optionally, the uncertainty the source states.

It holds no names or identifiers: subjects, sources and datasets are separate concepts. A clock reading finer than its stated resolution is refused, not rounded, and a stated uncertainty must be positive.

**`ChartSettings`** must name the time scale and the aspect angles. Points default to all ten bodies and four lunar points.

**The calculation key** is the SHA-256 of `calculation_inputs()`: canonical JSON (sorted keys, no whitespace, `chart-key-v1`) of:
- the whole record, the settings and the ephemeris provenance;
- the convention strings;
- the tzdata and engine versions.

How it behaves:
- It can be computed without calculating anything, and it's the same in any process.
- Any single change of input gives a new key, including record metadata. So a cache keyed on it can miss, but never returns a result computed from other inputs.
- Equal values written differently encode the same: 90 or 90.0, −0.0 or 0.0, and any zero-offset UTC tzinfo. The same instant written as a different kind of civil time does not.
- A field added to a record or to the settings without being encoded raises.

## 9. Two-chart comparison (`comparison.py`)

`compare(a, b)` gives B − A for every number the two charts hold, and keeps both charts beside the changes.
- **Wrapping.** Changes in longitude, ARMC and a pair's signed difference are wrapped into (−180°, 180°].
- **Deviations.** A deviation changes by exactly the change in separation, whatever the aspect angle.
- **Angle contacts.** Pairs involving the Ascendant, MC or Vertex are grouped by `involving_angles()`. All three angles turn with the Earth, so those contacts aren't independent measurements.
- **Refusals.** Charts computed under different settings, ephemeris data or conventions are refused. Different places are allowed.

## 10. Birth-time sensitivity (`sensitivity.py`)

`sweep(eph, record, settings, offsets)` recomputes a whole chart at each offset and compares it with the record's own chart. Estimating from the speeds instead would be off by up to 197″ on the Ascendant at +10 minutes, and by 4.7° for a pair passing an exact opposition.
- **Offsets** shift the resolved UTC instant by clock time, so a sweep never lands on a nonexistent or ambiguous local time. They're sorted, zero is added, repeats are refused, and there's no default set.
- **Series.** There's one series per point (longitude, declination) and per pair (signed difference, separation, deviation per aspect angle, declination difference and sum). Each series holds:
  - the values;
  - the changes;
  - the range of change;
  - the rate at the record's time, which is only a local slope.
- **No sensitivity threshold.** `by_range()` orders the series from most to least sensitive.
- **Rotation-driven series.** Series involving an angle are marked `rotation_driven`, because local sidereal time fixes all three angles: for statistics they're one factor.
- **Discontinuities.** A sweep that crosses Swiss Ephemeris's sidereal-time switch (§6) is flagged. The Moon file's segment steps (§5) can't be predicted from dates and aren't flagged.

## 11. Eclipses (`eclipses.py`)

`nearest_eclipses(eph, birth, solar_types=…, lunar_types=…)` gives the nearest solar and lunar eclipses before and after an instant. `next_eclipse` and `previous_eclipse` give one at a time.
- **Swiss Ephemeris finds and classifies them:**
  - type and central flag;
  - magnitudes (solar: NASA's definition; lunar: umbral, which is 0 for a penumbral eclipse, and penumbral separately);
  - saros series and member;
  - for solar eclipses, the place of greatest eclipse.
- **Greatest eclipse uses NASA's definition,** refined to about 0.1 s on Swiss Ephemeris's apparent geocentric positions:
  - **solar:** Earth's centre is closest to the shadow axis (the line through the Moon, away from the Sun);
  - **lunar:** the Moon is closest, in geocentric angle, to the point opposite the Sun.

  Swiss Ephemeris's own instant differs from the refined one by up to 9.6 s for solar eclipses, because its definition differs. For lunar eclipses it differs by several seconds: the definition differs too, and its search stops up to 4 s short of its own exact minimum. The refined instants reproduce NASA's canon for 1901–2100 within 0.55 s (solar; NASA rounds to whole seconds) and 2.75 s (lunar).
- **The syzygy** is the exact new or full moon, where the apparent geocentric longitudes are equal or 180° apart. It falls up to 18 minutes from greatest eclipse, which moves the Moon's longitude by up to 9.5′ for lunar eclipses.
- **Each instant** carries the Sun's and Moon's full positions, so eclipse longitudes and declinations come from §4.
- **An eclipse's degree and declination** (project convention, for rules built on the engine) are those at the syzygy: the Sun's for a solar eclipse, the Moon's for a lunar one. Greatest eclipse remains the eclipse's instant.
- **Before and after** are strict, in TT, and decided by the refined instant.
- **Which types count** is the caller's choice. `SOLAR_TYPES` and `LUNAR_TYPES` are named sets of every type of each kind.
- **Refusals.** Eclipses outside the data files are refused.

## 12. Parans, proof of concept (`parans.py`)

`angular_events(eph, place, start=…, end=…, bodies=…, stars=…, horizon=…)` lists every rise, set, upper and lower meridian transit in [start, end). `paran_gaps(events)` gives the time gap and the ARMC gap for every pair of events of two different points. There's no orb, and nothing marks a pair as a paran.
- **Positions.** The body's actual position, ecliptic latitude included, topocentric, as Swiss Ephemeris's `rise_trans` computes it. This is the project's chosen paran definition. The zodiacal definition isn't covered, because Swiss Ephemeris's transits always use the body's actual right ascension.
- **Window.** The caller passes `start` and `end`. The project convention is 24 hours centred on the birth (birth ± 12 h), which is defined at every latitude; Phase 2 will add it as a named helper.
- **Horizon convention**, named by the caller:
  - the disc point for the Sun and Moon (upper limb, centre or lower limb; stars are points);
  - refraction, as a stated pressure (> 0 hPa) and temperature, or none for the geometric horizon.
- **Transits** are at hour angle 0° and 180°.
- **Every event is refined** by a second search started 3 minutes before the first result:
  - **star transits:** the first answer can be up to 2.6 s off, because Swiss Ephemeris holds a star's position fixed at the search start;
  - **Moon transits:** up to 0.69 s off, because the search stops after four iterations;
  - **the Moon's rise and set:** up to 0.11 s off.

  Refined events agree with ERFA within 7.3 ms, close to the 7 ms resolution of Swiss Ephemeris's bisection.
- **Bodies Swiss Ephemeris says don't rise or set** are listed in `no_rise_or_set`. It can't tell circumpolar from never rising, and the result doesn't either. Their transits are kept, and a body that rises again later in the window is still followed.
- **Stars** are named by catalogue designation (e.g. `,alLeo`), because names repeat in the catalogue.
- **Refusals.** Windows outside the data files and the poles are refused.

## 13. Numerical precision and validation

Results are Swiss Ephemeris's doubles, never rounded. Tolerances in the tests are derived from rounding, from the method's own resolution, or from an independent reference's documented accuracy. Where a bound was measured instead of derived, the test says so. Independent references, never another program that also uses Swiss Ephemeris:

| What | Reference | Budget |
|---|---|---|
| Ephemeris data and light-time (layer 1) | JPL Horizons, geocentric astrometric RA/Dec (ICRF), 10 bodies × 9 dates 1800–2399 | 2 mas on the sky, × max(1, r_helio/r_geo) for planets |
| Precession, obliquity and nutation models (layer 2) | ERFA: Vondrák 2011 precession, IAU 2006 obliquity, IAU 2000A nutation | 1.32 mas, 0.51 mas, 1.0 mas |
| Apparent positions end to end (layer 3) | Horizons + ERFA's apparent-place pipeline | layer 1 budget + 1.0 mas (nutation) + 0.5 mas (obliquity) |
| Lunar points (layer 4) | Osculating elements from Horizons' geometric Moon vectors; ERFA's fundamental arguments | true node 0.1″, osculating apogee 0.9″, mean points 1.5″ (mean apogee latitude 0.1″) |
| ARMC | ERFA `gst06a`, 1850–2050 | 1 mas |
| Angles | Their geometric definitions (eastern horizon, upper meridian, due west) | 1 × 10⁻⁹° |
| Eclipses | NASA Five Millennium Canon, 909 eclipses 1901–2100 | identity, type and saros exact; times and magnitudes within the largest observed differences (labelled as measured) |
| Star rise, set and transits | ERFA `atco13`, refraction off | 0.01 s |
| Body rise, set and transits | ERFA's horizon transform of Swiss Ephemeris's topocentric positions, refraction off | 0.01 s |

A residual above its budget is a finding to investigate, never a reason to widen the tolerance.

## Appendix: convention strings

Recorded verbatim in results and calculation keys:

- `positions-v2: geocentric, apparent (light-time, aberration, solar light deflection); tropical, true ecliptic and true equinox of date; Jupiter-Pluto as system barycenters; mean node/apogee from Moshier-ELP2000-85, true node/osculating apogee from the Moon's osculating orbit with GM(Earth+Moon)`
- `angles-v1: tropical, true ecliptic of date, from UT1; Swiss Ephemeris sidereal time (IERS 2010 for 1850-01-01..2050-01-01, its long-term model outside); Ascendant always the eastern rising point, MC on the upper meridian even below the horizon, Vertex always due west; Descendant and IC = Ascendant and MC + 180`
- `measurements-v1: separations in ecliptic longitude, signed difference b - a in (-180, 180]; deviation = separation - aspect angle; rates per day, negative while closing; parallels by declination difference and sum; points are the bodies and lunar points, then Ascendant, MC and Vertex (no Descendant or IC); angle declinations from the true obliquity`
- `eclipses-v1: found and classified by Swiss Ephemeris (type, central, magnitudes, saros, place); greatest eclipse refined to NASA's definition on apparent geocentric positions (solar: Earth's centre closest to the shadow axis; lunar: Moon closest in angle to the anti-Sun point), to about 0.1 s; syzygy: apparent longitudes equal or opposite; before and after strictly, in TT; lunar magnitude umbral, 0 when penumbral`
- `parans-v1: Swiss Ephemeris rise_trans, topocentric, the body's actual position (ecliptic latitude included); rise and set of the chosen disc point with the chosen refraction (none: geometric horizon); transits at hour angle 0 and 180; each event refined by a second search from 3 minutes before; events in [start, end); gaps in time and ARMC, no orb`
- Calculation key schema: `chart-key-v1`
