// Makes this folder's fixtures with AstroChart Bridge's own format module, so the platform's
// reader is checked against the format's reference implementation, never against itself.
//
//   node make.mjs <astrochart-bridge checkout>/extension/core/portable.js
//
// It writes five chart files, as the extension writes them (serialize(chart, 2) and a newline,
// named chart-<date>.astrochart.json), and conformance.json: inputs a reader must get right,
// each with what AstroChart Bridge's parse() made of it. Every chart is synthetic.

import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const [modulePath] = process.argv.slice(2);
if (!modulePath) throw new Error("usage: node make.mjs <path to portable.js>");
const { parse, serialize, toPortable } = await import(pathToFileURL(modulePath).href);
const here = dirname(fileURLToPath(import.meta.url));

const charts = [
  [
    { local: "1987-06-15T04:25:00", utc: "1987-06-15T03:25:00Z", lat: 51.5074, lng: -0.1278 },
    { site: "astro-seek", via: "page", zoneLabel: "BST", utcBasis: "printed" },
    { name: "Synthetic Person One", town: "London, UK" },
  ],
  [
    { local: "1990-01-01T12:00:00", utc: "1990-01-01T06:30:00Z", lat: 28.6139, lng: 77.209 },
    { site: "astro-com", via: "page", zoneLabel: "5h30e", utcBasis: "zone-label" },
    { name: "Synthetic Person Two" },
  ],
  [
    // Paris local mean time, 0:09:21 east, to the second.
    { local: "1890-03-01T10:00:00", utc: "1890-03-01T09:50:39Z", lat: 48.8566, lng: 2.3522 },
    { site: "astro-com", via: "page", zoneLabel: "LMT", utcBasis: "printed" },
    {},
  ],
  [
    // Half past midnight in Sydney is the previous day in UTC.
    { local: "2000-01-01T00:30:00", utc: "1999-12-31T13:30:00Z", lat: -33.8688, lng: 151.2093 },
    { site: "astro-seek", via: "screenshot", zoneLabel: "AEDT", utcBasis: "zone-label" },
    { name: "Synthetic Person Three", town: "Sydney, AU" },
  ],
  [
    { local: "1975-07-04T16:45:00", utc: "1975-07-04T20:45:00Z", lat: 40.7128, lng: -74.006 },
    { via: "paste", utcBasis: "entered" },
    { name: "Synthetic Person Four" },
  ],
];

for (const [sky, source, labels] of charts) {
  const made = toPortable(sky, source, labels);
  if (made.kind !== "portable") throw new Error(`refused: ${made.reason}`);
  const name = `chart-${sky.local.slice(0, 10)}.astrochart.json`;
  writeFileSync(join(here, name), `${serialize(made.chart, 2)}\n`);
}

// The cases start from one chart and change one thing.
const base = () => ({
  format: "astrochart",
  version: 1,
  chart: {
    calendar: "gregorian",
    local: "1987-06-15T04:25:00",
    utcOffsetSeconds: 3600,
    latitude: 51.5074,
    longitude: -0.1278,
  },
  source: { site: "astro-seek", via: "page", zoneLabel: "BST", utcBasis: "printed" },
  labels: { name: "Synthetic Person One" },
});
const changed = (change) => {
  const document = base();
  change(document);
  return JSON.stringify(document);
};
// JSON text with a value JSON.stringify can't write, put in by hand.
const withRaw = (path, raw) => {
  const marker = "__RAW__";
  const text = changed((document) => {
    let section = document;
    for (const key of path.slice(0, -1)) section = section[key];
    section[path.at(-1)] = marker;
  });
  return text.replace(`"${marker}"`, raw);
};
// Exactly `length` UTF-16 code units, as a JavaScript string counts them, padded inside an
// unknown source field with `fill` (one or two units) and "x" for an odd unit left over.
const padded = (length, fill) => {
  const text = changed((document) => {
    document.source.note = "";
  });
  const room = length - text.length;
  const fills = Math.floor(room / fill.length);
  const padding = fill.repeat(fills) + "x".repeat(room - fills * fill.length);
  return text.replace('"note":""', `"note":"${padding}"`);
};

const cases = {
  "accepted as written": JSON.stringify(base()),
  "not JSON": "{",
  "an array": "[]",
  "null": "null",
  "a NaN literal": withRaw(["chart", "latitude"], "NaN"),
  "another format": changed((d) => { d.format = "other"; }),
  "version as text": changed((d) => { d.version = "1"; }),
  "version 2": changed((d) => { d.version = 2; }),
  "version written 1.0": withRaw(["version"], "1.0"),
  "version true": changed((d) => { d.version = true; }),
  "an unknown top-level key": changed((d) => { d.approximate = true; }),
  "chart as an array": changed((d) => { d.chart = []; }),
  "an unknown chart key": changed((d) => { d.chart.zone = "Europe/London"; }),
  "the Julian calendar": changed((d) => { d.chart.calendar = "julian"; }),
  "a space for T": changed((d) => { d.chart.local = "1987-06-15 04:25:00"; }),
  "30 February": changed((d) => { d.chart.local = "1987-02-30T04:25:00"; }),
  "no seconds": changed((d) => { d.chart.local = "1987-06-15T04:25"; }),
  "hour 24": changed((d) => { d.chart.local = "1987-06-15T24:00:00"; }),
  "Arabic-Indic digits": changed((d) => { d.chart.local = "١٩٨٧-06-15T04:25:00"; }),
  "a trailing newline": changed((d) => { d.chart.local = "1987-06-15T04:25:00\n"; }),
  "year 0050": changed((d) => { d.chart.local = "0050-06-15T04:25:00"; }),
  "the day before the Gregorian calendar": changed((d) => { d.chart.local = "1582-10-14T23:59:59"; }),
  "the Gregorian calendar's first day": changed((d) => { d.chart.local = "1582-10-15T00:00:00"; }),
  "a UTC past year 9999": changed((d) => {
    d.chart.local = "9999-12-31T23:00:00";
    d.chart.utcOffsetSeconds = -43200;
  }),
  "an offset with a fraction": changed((d) => { d.chart.utcOffsetSeconds = 3600.5; }),
  "an offset written 3600.0": withRaw(["chart", "utcOffsetSeconds"], "3600.0"),
  "an offset as text": changed((d) => { d.chart.utcOffsetSeconds = "3600"; }),
  "an offset true": changed((d) => { d.chart.utcOffsetSeconds = true; }),
  "the lowest offset": changed((d) => { d.chart.utcOffsetSeconds = -43200; }),
  "below the lowest offset": changed((d) => { d.chart.utcOffsetSeconds = -43201; }),
  "the highest offset": changed((d) => { d.chart.utcOffsetSeconds = 50400; }),
  "above the highest offset": changed((d) => { d.chart.utcOffsetSeconds = 50401; }),
  "a latitude past 90": changed((d) => { d.chart.latitude = 90.000001; }),
  "a latitude as text": changed((d) => { d.chart.latitude = "51.5"; }),
  "a latitude true": changed((d) => { d.chart.latitude = true; }),
  "a latitude 1e400": withRaw(["chart", "latitude"], "1e400"),
  "longitude -180": changed((d) => { d.chart.longitude = -180; }),
  "longitude written -0": withRaw(["chart", "longitude"], "-0"),
  "latitude written -0.0": withRaw(["chart", "latitude"], "-0.0"),
  "source as text": changed((d) => { d.source = "astro-seek"; }),
  "a site with a space": changed((d) => { d.source.site = "astro seek"; }),
  "a site of 33 characters": changed((d) => { d.source.site = "a".repeat(33); }),
  "a zone label with a newline": changed((d) => { d.source.zoneLabel = "BST\nDST"; }),
  "a zone label of 65 characters": changed((d) => { d.source.zoneLabel = "B".repeat(65); }),
  "a zone label of 32 emoji": changed((d) => { d.source.zoneLabel = "🕐".repeat(32); }),
  "a zone label of 33 emoji": changed((d) => { d.source.zoneLabel = "🕐".repeat(33); }),
  "an unknown source key": changed((d) => { d.source.reliability = "AA"; }),
  "no source": changed((d) => { delete d.source; }),
  "labels as text": changed((d) => { d.labels = "Synthetic"; }),
  "a name of spaces": changed((d) => { d.labels.name = "   "; }),
  "a name with a zero-width joiner": changed((d) => { d.labels.name = "Synthetic‍Person"; }),
  "a name with a right-to-left override": changed((d) => { d.labels.name = "Synthetic‮Person"; }),
  "a name of 101 characters": changed((d) => { d.labels.name = "n".repeat(101); }),
  "a name null": changed((d) => { d.labels.name = null; }),
  "a name with a lone surrogate": withRaw(["labels", "name"], '"Synthetic\\ud800"'),
  "an unknown label key": changed((d) => { d.labels.nickname = "Syn"; }),
  "empty labels": changed((d) => { d.labels = {}; }),
  "16,384 characters": padded(16_384, "x"),
  "16,385 characters": padded(16_385, "x"),
  "16,384 UTF-16 units of emoji": padded(16_384, "🕐"),
  "16,386 UTF-16 units of emoji": padded(16_386, "🕐"),
};

const conformance = Object.entries(cases).map(([name, text]) => {
  const read = parse(text);
  return read.kind === "portable"
    ? { name, text, read: read.chart }
    : { name, text, refused: read.reason };
});
writeFileSync(join(here, "conformance.json"), `${JSON.stringify(conformance, null, 2)}\n`);
