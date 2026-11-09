# Result format: `chart-result-v1`

A chart, as `compute_chart()` returns it, written as one JSON document and read back exactly. The format is defined in `src/astro_research/result_format.py`:

- `chart_to_json(chart)` returns compact JSON text;
- `chart_from_json(text)` returns a `Chart` equal to the one written, or raises `InvalidChartDocument` (a `ValueError`).

This document and the strict reader are the format's schema. There's no separate JSON Schema file yet.

**What it covers.** Charts only. Comparisons and sensitivity sweeps are computed from charts and can be computed again from documents (a chart takes about 0.5 ms, measured). Eclipse and paran results will get a format when they get calculation keys.

## Layout

A document is an object with these sections, always in this order. Each value is written once, with one exception: a body's chart point repeats five of the body's values, as the engine computes it.

| Section | Holds |
|---|---|
| `format` | `"chart-result-v1"` |
| `calculation_key` | The chart's calculation key, the SHA-256 of `inputs` |
| `inputs` | `calculation_inputs()` verbatim: the record, settings, ephemeris provenance, convention strings, tzdata and engine versions (`CALCULATION_CONVENTIONS.md` §8) |
| `time` | The resolved instant and its Julian dates |
| `bodies` | The settings' bodies and lunar points, in their order |
| `angles` | The angles, with the ARMC and true obliquity |
| `points` | The chart points: the bodies, then the Ascendant, MC and Vertex |
| `pairs` | Every pair of points once, in order, naming its two points |

**`time`:**
- `utc`: the UTC reading, ISO 8601 with its offset (`+00:00`);
- `utc_offset_us`: the local offset in whole microseconds;
- `offset_source`: `utc_given`, `tzdb`, `stated` or `local_mean_time`;
- `zone_abbreviation` and `tzdata_version`: from tzdb, or `null`;
- `time_scale`, which must be the settings' time scale;
- `jd_ut1`, `jd_tt`, `delta_t_s` and `ut1_minus_utc_s`;
- `ut1_basis`: where UT1 came from (`iers_c04`, `delta_t_model`, `civil_time_as_ut` or `clock_reading`).

The record's own civil time isn't repeated here: it's in `inputs`.

**`bodies`:** `body`, then `longitude`, `latitude`, `distance_au`, `longitude_speed`, `latitude_speed`, `distance_speed`, `right_ascension`, `declination`, `right_ascension_speed` and `declination_speed`.

**`angles`:** `armc`, `armc_speed`, `ascendant`, `ascendant_speed`, `midheaven`, `midheaven_speed`, `descendant`, `imum_coeli`, `vertex`, `vertex_speed`, `true_obliquity` and `beyond_polar_circle`. Their time, place, provenance and conventions are the chart's.

**`points`:** `point`, then `longitude`, `latitude`, `declination`, `longitude_speed` and `declination_speed`.

**`pairs`:** `a` and `b` (point names), `longitude_difference`, `separation`, `separation_rate`, `great_circle_separation`, then `deviations`, then `declination_difference`, `declination_sum`, `declination_difference_rate` and `declination_sum_rate`. `deviations` holds one entry per aspect angle, in the settings' order: `angle`, `deviation` and `deviation_rate`.

Units are as in `CALCULATION_CONVENTIONS.md`: degrees, AU, rates per day, seconds, Julian days. Names are the engine's enum values (`sun`, `true_node`, `ascendant` and so on).

## Values

- **Floats** are written as Python's shortest repr, which reads back bit for bit. All 5,565 floats of a full chart did (observed), and −0.0 keeps its sign. Inside `inputs`, the calculation key writes −0.0 as 0.0, so a record read back has 0.0 there; the two compare equal.
- **NaN and infinities** are refused when writing. The engine doesn't produce them.
- **Instants** are ISO 8601. **Durations** are whole microseconds, as in the calculation key.

## Reading

Reading is strict and repairs nothing. A document is refused when:
- it isn't a JSON object, or its `format` isn't `chart-result-v1`;
- the SHA-256 of its `inputs`, as stored, isn't its `calculation_key`;
- the inputs' key schema isn't `chart-key-v1`;
- a section or field is missing, or one is there that the format doesn't have;
- a value has the wrong type. An integer or a boolean is never a float, since this format writes every float with a point or an exponent;
- a name is unknown;
- the structure isn't one the engine produces. That covers bodies other than the settings' points, points out of order, a body's point that doesn't repeat its body's values, a pair naming a point the chart lacks, pairs missing or out of order, deviations for angles other than the settings', and a time scale other than the settings';
- the engine itself refuses the record or settings, for example a latitude of 95°.

**The inputs are hashed as stored,** not rebuilt with today's code, so a chart written by another engine version reads as it was written. Its inputs name that version.

**What reading doesn't check:**
- **The result values.** They aren't covered by the calculation key. Checking them means computing the chart again from its inputs, which belongs with storage.
- **Today's environment.** A chart computed with other ephemeris data or conventions reads back as it was written, and its provenance says what it was computed with.

## Writing

`chart_to_json` refuses two kinds of chart, which the engine never builds:
- **A part with a field the format doesn't write** raises `TypeError` naming the field. A field added to a result later must be added to the format too, or it would be dropped.
- **Two copies of one value that disagree** raise `ValueError`, since the document keeps only one. That covers a calculation key that isn't its inputs' SHA-256, and the time, time scale, place, provenance or conventions held differently in different parts. It also covers a pair's point that isn't the chart's point, and a body's point that doesn't repeat its body.

## Size and speed

For a full chart (17 points, 136 pairs, 7 aspect angles; measured):
- **Size:** 138 KiB, or 24 KiB compressed with zlib. Mirroring the Python objects field for field would take 186 KiB, 24.5 KiB compressed, because each pair would repeat both its points. About 90% of either is the pairs.
- **Speed:** writing takes about 2.0 ms and reading about 2.2 ms. Computing the chart takes 0.51 ms, so reading a stored chart back is about four times slower than computing it again.

## Versions

Any change to the layout gets a new format name, and a reader refuses names it doesn't know. A document records the engine version and conventions it was computed with, inside `inputs`.

## Example

The README's chart, abridged: the inputs, all but the first body, point and pair, and all but the first deviation are left out.

```json
{
  "format": "chart-result-v1",
  "calculation_key": "f8bcf7465ae38608fe1ca00fbba029565041f7f59bf70ec57d4ccaeaa43cd530",
  "inputs": {"...": "calculation_inputs(), verbatim"},
  "time": {
    "utc": "1987-06-15T03:25:00+00:00",
    "utc_offset_us": 3600000000,
    "offset_source": "tzdb",
    "zone_abbreviation": "BST",
    "tzdata_version": "2026.6",
    "time_scale": "ut1_from_iers",
    "jd_ut1": 2446961.6423566663,
    "jd_tt": 2446961.642999815,
    "delta_t_s": 55.56805095104155,
    "ut1_minus_utc_s": -0.3840509510415515,
    "ut1_basis": "iers_c04"
  },
  "bodies": [
    {
      "body": "sun",
      "longitude": 83.52625537247525,
      "latitude": -0.00019723535907500644,
      "distance_au": 1.0157447237418324,
      "longitude_speed": 0.9548916492325158,
      "latitude_speed": 3.487023725644417e-05,
      "distance_speed": 0.00010029580512228079,
      "right_ascension": 82.9494319145575,
      "declination": 23.28473510523447,
      "right_ascension_speed": 1.0383200902717709,
      "declination_speed": 0.04666512785851709
    }
  ],
  "angles": {
    "armc": 314.0062914273927,
    "armc_speed": 360.98564733498665,
    "ascendant": 77.06172744980078,
    "ascendant_speed": 396.1389040698607,
    "midheaven": 311.54640634007137,
    "midheaven_speed": 358.58140720908096,
    "descendant": 257.0617274498008,
    "imum_coeli": 131.54640634007137,
    "vertex": 215.43672831808306,
    "vertex_speed": 287.8854328515827,
    "true_obliquity": 23.44326150274944,
    "beyond_polar_circle": false
  },
  "points": [
    {
      "point": "sun",
      "longitude": 83.52625537247525,
      "latitude": -0.00019723535907500644,
      "declination": 23.28473510523447,
      "longitude_speed": 0.9548916492325158,
      "declination_speed": 0.04666512785851709
    }
  ],
  "pairs": [
    {
      "a": "sun",
      "b": "moon",
      "longitude_difference": -133.80986335733076,
      "separation": 133.80986335733076,
      "separation_rate": -13.82787361224702,
      "great_circle_separation": 133.65575655226417,
      "deviations": [
        {"angle": 0.0, "deviation": 133.80986335733076, "deviation_rate": -13.82787361224702}
      ],
      "declination_difference": -45.238651285737426,
      "declination_sum": 1.330818924731517,
      "declination_difference_rate": 4.6430687672753095,
      "declination_sum_rate": 4.7363990229923445
    }
  ]
}
```
