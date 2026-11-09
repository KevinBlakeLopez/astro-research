"""Swiss Ephemeris adapter: the only module that imports `swisseph`.

It starts with the one-time environment check that every calculation depends on. The engine
fails closed (decision D1), so `verify_ephemeris()` confirms, in order:

1. The `swisseph` module is the pysweph build the project pins, byte for byte as installed.
2. The data folder holds exactly the manifest's files, each matching its SHA-256.
3. Swiss Ephemeris, asked for its full-precision ephemeris, really loaded those files: the
   returned flag says so, and each loaded file lies inside the folder with the manifest's DE
   number. That catches an SE_EPHE_PATH override, which takes priority over set_ephe_path.

It then checks the packaged IERS UT1 table against the manifest's SHA-256, and the leap seconds
Swiss Ephemeris holds against that table.

The ephemeris path is process-wide state in Swiss Ephemeris; this module owns it. Calculations
go through an `Ephemeris` handle, which only `Ephemeris.open()` creates (after the check) and
which refuses to calculate once the path has moved elsewhere. Swiss Ephemeris's delta-T setting
is process-wide too: this module sets it only for single calls, and restores the model after.
"""

import base64
import bisect
import contextlib
import functools
import hashlib
import importlib.metadata
import importlib.resources
import os
import re
import tomllib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import assert_never

import swisseph as swe

from astro_research.angles import ANGLE_CONVENTIONS, Angles, AnglesUndefined
from astro_research.astronomical_time import (
    AstronomicalTime,
    InstantInLeapSecond,
    TimeScale,
    Ut1Basis,
)
from astro_research.civil_time import ResolvedInstant, UtcTime, resolve
from astro_research.eclipses import (
    EclipseAttributes,
    EclipseCandidate,
    EclipseKind,
    EclipseType,
)
from astro_research.geo import GeoPosition
from astro_research.parans import (
    Disc,
    FixedStar,
    HorizonConvention,
    HorizonEvent,
    StarNotFound,
)
from astro_research.positions import (
    POSITION_CONVENTIONS,
    Body,
    BodyPosition,
    LunarPoint,
    Point,
    Positions,
)

_PROBE_JD_UT = 2451545.0  # 2000-01-01 12:00 UT, inside the pinned files
_JD_1972 = 2441317.5  # 1972-01-01 0h UTC: UTC with leap seconds from then; civil time was UT before
_MJD_EPOCH = date(1858, 11, 17)
_SCAN_END = date(2400, 1, 1)  # the pinned planet file ends 2400-01-10 TT
_ROUND_TRIP_DAYS = 1e-3 / 86400  # a time from TT must come back to it: rounding is about 40 us
_active_folder: Path | None = None  # the verified folder Swiss Ephemeris currently points at
_OPEN = object()  # only Ephemeris.open() holds this, so only it can construct a handle
_SE_POINT: dict[Point, int] = {
    Body.SUN: swe.SUN,
    Body.MOON: swe.MOON,
    Body.MERCURY: swe.MERCURY,
    Body.VENUS: swe.VENUS,
    Body.MARS: swe.MARS,
    Body.JUPITER: swe.JUPITER,
    Body.SATURN: swe.SATURN,
    Body.URANUS: swe.URANUS,
    Body.NEPTUNE: swe.NEPTUNE,
    Body.PLUTO: swe.PLUTO,
    LunarPoint.MEAN_NODE: swe.MEAN_NODE,
    LunarPoint.TRUE_NODE: swe.TRUE_NODE,
    LunarPoint.MEAN_APOGEE: swe.MEAN_APOG,
    LunarPoint.OSCULATING_APOGEE: swe.OSCU_APOG,
}
# Swiss Ephemeris's defaults are the adopted conventions (see positions.py); only the data
# source (the verified files) and speeds are requested explicitly. Requesting speeds changes the
# equatorial values only by float rounding (< 1e-6 mas, measured).
_ECLIPTIC_FLAGS = swe.FLG_SWIEPH | swe.FLG_SPEED
_EQUATORIAL_FLAGS = swe.FLG_SWIEPH | swe.FLG_EQUATORIAL | swe.FLG_SPEED
_VECTOR_FLAGS = swe.FLG_SWIEPH | swe.FLG_EQUATORIAL | swe.FLG_XYZ  # apparent, geocentric, AU
_NOWHERE = GeoPosition(latitude=0.0, longitude=0.0)  # a UTC input does not depend on the place
_SOLAR_TYPE_FLAGS = {
    swe.ECL_TOTAL: EclipseType.TOTAL,
    swe.ECL_ANNULAR: EclipseType.ANNULAR,
    swe.ECL_ANNULAR_TOTAL: EclipseType.HYBRID,
    swe.ECL_PARTIAL: EclipseType.PARTIAL,
}
_LUNAR_TYPE_FLAGS = {
    swe.ECL_TOTAL: EclipseType.TOTAL,
    swe.ECL_PARTIAL: EclipseType.PARTIAL,
    swe.ECL_PENUMBRAL: EclipseType.PENUMBRAL,
}
_NO_SAROS = -99999999  # Swiss Ephemeris's value when it has no saros number
_RISE_TRANS_EVENT = {
    HorizonEvent.RISE: swe.CALC_RISE,
    HorizonEvent.SET: swe.CALC_SET,
    HorizonEvent.UPPER_TRANSIT: swe.CALC_MTRANSIT,
    HorizonEvent.LOWER_TRANSIT: swe.CALC_ITRANSIT,
}
_RISE_TRANS_DISC = {
    Disc.UPPER_LIMB: 0,  # Swiss Ephemeris's default
    Disc.CENTRE: swe.BIT_DISC_CENTER,
    Disc.LOWER_LIMB: swe.BIT_DISC_BOTTOM,
}
# Swiss Ephemeris keeps the first star catalogue it reads for the rest of the process: neither
# set_ephe_path nor close reloads it (observed). So the check compares one star's catalogue
# position, as Swiss Ephemeris holds it in memory, with the pinned file's record. Barycentric,
# ICRS, at J2000.0 TT, with no light-time, aberration, deflection or nutation, it returns the
# record exactly (observed: 0.000 mas).
_STAR_PROBE = "alLeo"
_STAR_CATALOGUE_FLAGS = (
    swe.FLG_SWIEPH
    | swe.FLG_EQUATORIAL
    | swe.FLG_BARYCTR
    | swe.FLG_ICRS
    | swe.FLG_J2000
    | swe.FLG_TRUEPOS
    | swe.FLG_NOABERR
    | swe.FLG_NOGDEFL
    | swe.FLG_NONUT
)
_STAR_PROBE_TOLERANCE_DEG = 1e-9  # float conversion of the record's sexagesimal values


class EphemerisEnvironmentError(RuntimeError):
    """The calculation environment is not the pinned one; nothing may be calculated."""


class BindingMismatch(EphemerisEnvironmentError):
    """The `swisseph` module is not the pysweph build the project pins."""


class EphemerisFileError(EphemerisEnvironmentError):
    """The data folder does not hold exactly the manifest's files."""


class EphemerisNotLoaded(EphemerisEnvironmentError):
    """Swiss Ephemeris did not load the verified files at full precision."""


class StaleEphemeris(EphemerisEnvironmentError):
    """The process-wide data path has moved since this handle was opened."""


class DateOutsideEphemeris(EphemerisEnvironmentError):
    """The requested date needs data files the manifest does not pin."""


class DateOutsideUt1Data(DateOutsideEphemeris):
    """The instant is after the packaged IERS UT1 table's last day; name another time scale."""


class DateOutsideLeapSeconds(DateOutsideEphemeris):
    """Under UT1_FROM_DELTA_T, the instant is where Swiss Ephemeris stops applying leap seconds."""


@dataclass(frozen=True, kw_only=True)
class ManifestFile:
    name: str
    covers: str
    size: int
    sha256: str
    header: str


@dataclass(frozen=True, kw_only=True)
class Ut1DataEntry:
    name: str
    source_url: str
    source_sha256: str
    source_last_modified: str
    first_day: str
    last_day: str
    size: int
    sha256: str


@dataclass(frozen=True, kw_only=True)
class EphemerisManifest:
    de_number: int
    source_commit: str
    url_template: str
    files: tuple[ManifestFile, ...]
    ut1_data: Ut1DataEntry

    def url(self, name: str) -> str:
        return self.url_template.format(commit=self.source_commit, name=name)


@dataclass(frozen=True, kw_only=True)
class EphemerisProvenance:
    binding: str  # e.g. "pysweph 2.10.3.6"
    swisseph_version: str  # C library string; unchanged across the 2022-2026 data builds
    de_number: int  # as reported by Swiss Ephemeris for the loaded files
    source_commit: str
    file_sha256: tuple[tuple[str, str], ...]  # (file name, verified SHA-256)
    ut1_data_sha256: str  # the packaged IERS UT1 table, verified


def load_manifest() -> EphemerisManifest:
    resource = importlib.resources.files("astro_research").joinpath("ephemeris_manifest.toml")
    raw = tomllib.loads(resource.read_text())
    return EphemerisManifest(
        de_number=raw["de_number"],
        source_commit=raw["source_commit"],
        url_template=raw["url_template"],
        files=tuple(ManifestFile(**entry) for entry in raw["files"]),
        ut1_data=Ut1DataEntry(**raw["ut1_data"]),
    )


def verify_ephemeris(ephe_dir: Path) -> EphemerisProvenance:
    """Run the one-time check and point Swiss Ephemeris at `ephe_dir`; raise if anything is off."""
    manifest = load_manifest()
    binding = _verify_binding()
    _verify_files(ephe_dir, manifest)
    _verify_loaded(ephe_dir, manifest)
    return EphemerisProvenance(
        binding=binding,
        swisseph_version=swe.version,
        de_number=manifest.de_number,
        source_commit=manifest.source_commit,
        file_sha256=tuple((f.name, f.sha256) for f in manifest.files),
        ut1_data_sha256=manifest.ut1_data.sha256,
    )


@dataclass(frozen=True, kw_only=True)
class Ut1Table:
    """IERS's daily UT1 - UTC, held as delta-T at each day's 0h UTC.

    Delta-T (TT - UT1) has no leap-second steps, so interpolating it in TT runs smoothly across
    a leap second, where UT1 - UTC jumps by a whole second.
    """

    first_day: date
    last_day: date
    tt: tuple[float, ...]  # TT Julian date of each day's 0h UTC
    delta_t_s: tuple[float, ...]  # delta-T there: 32.184 s + (TAI - UTC) - (UT1 - UTC)

    @property
    def first_ut1(self) -> float:
        """UT1 Julian date of the first day's 0h UTC."""
        return self.tt[0] - self.delta_t_s[0] / 86400

    def delta_t_at_tt(self, jd_tt: float) -> float:
        """Delta-T in seconds at `jd_tt`, linear between days; the caller keeps it in range."""
        i = min(max(bisect.bisect_right(self.tt, jd_tt) - 1, 0), len(self.tt) - 2)
        fraction = (jd_tt - self.tt[i]) / (self.tt[i + 1] - self.tt[i])
        return self.delta_t_s[i] + fraction * (self.delta_t_s[i + 1] - self.delta_t_s[i])

    def tt_from_ut1(self, jd_ut1: float) -> float:
        """TT such that TT - delta-T(TT) = UT1. Delta-T changes by under 5 ms a day (since 1972,
        measured), so each step shrinks the error by a factor of 10^7; three are plenty."""
        jd_tt = jd_ut1 + self.delta_t_s[0] / 86400
        for _ in range(3):
            jd_tt = jd_ut1 + self.delta_t_at_tt(jd_tt) / 86400
        return jd_tt


def verify_ut1_data(source: Traversable) -> Ut1Table:
    """Check a UT1 table against the manifest's size and SHA-256, then read it."""
    entry = load_manifest().ut1_data
    data = source.read_bytes()
    if len(data) != entry.size or hashlib.sha256(data).hexdigest() != entry.sha256:
        raise EphemerisFileError(f"{source.name} does not match the manifest's ut1_data")
    first_day, last_day = date.fromisoformat(entry.first_day), date.fromisoformat(entry.last_day)
    rows = [line.split() for line in data.decode("ascii").splitlines() if not line.startswith("#")]
    expected = range((first_day - _MJD_EPOCH).days, (last_day - _MJD_EPOCH).days + 1)
    if [int(mjd) for mjd, _ in rows] != list(expected):
        raise EphemerisFileError(f"{source.name} lacks days between {first_day} and {last_day}")
    tt: list[float] = []
    delta_t_s: list[float] = []
    previous: tuple[int, float] | None = None  # the day before's TAI - UTC and UT1 - UTC
    for mjd, value in rows:
        day = _MJD_EPOCH + timedelta(days=int(mjd))
        ut1_minus_utc = float(value)
        jd_tt, _ = swe.utc_to_jd(day.year, day.month, day.day, 0, 0, 0.0, swe.GREG_CAL)
        tt_minus_utc = (jd_tt - (int(mjd) + 2400000.5)) * 86400
        tai_minus_utc = round(tt_minus_utc - 32.184)
        # Swiss Ephemeris counts leap seconds from its own table; where its delta-T model says
        # some are missing (from 2033-09-17, observed), it takes the reading as UT1 instead.
        if abs(tt_minus_utc - 32.184 - tai_minus_utc) > 1e-3:
            raise EphemerisNotLoaded(f"Swiss Ephemeris has no leap-second count for {day}")
        # A leap second shows in IERS's data as UT1 - UTC rising by about a second overnight;
        # otherwise it changes by under 5 ms a day (measured). Swiss Ephemeris's count must step
        # on exactly those days.
        if previous is not None:
            leap_second = ut1_minus_utc - previous[1] > 0.5
            if tai_minus_utc - previous[0] != int(leap_second):
                raise EphemerisNotLoaded(_leap_seconds_differ(day))
        previous = (tai_minus_utc, ut1_minus_utc)
        tt.append(jd_tt)
        delta_t_s.append(32.184 + tai_minus_utc - ut1_minus_utc)
    return Ut1Table(
        first_day=first_day, last_day=last_day, tt=tuple(tt), delta_t_s=tuple(delta_t_s)
    )


@functools.cache
def _packaged_ut1() -> Ut1Table:
    """The table inside the package, verified once per process and then kept in memory."""
    name = load_manifest().ut1_data.name
    return verify_ut1_data(importlib.resources.files("astro_research").joinpath(name))


def _verify_binding() -> str:
    pinned = _pinned_pysweph_version()
    try:
        dist = importlib.metadata.distribution("pysweph")
    except importlib.metadata.PackageNotFoundError:
        raise BindingMismatch("pysweph is not installed") from None
    if dist.version != pinned:
        raise BindingMismatch(f"pysweph {dist.version} is installed; the project pins {pinned}")
    # pyswisseph installs the same `swisseph` module file and silently overwrites pysweph's
    # (pip check still passes). The bytes on disk must match what pysweph recorded at install.
    recorded = [
        f
        for f in dist.files or ()
        if f.name.startswith("swisseph") and f.suffix in (".so", ".pyd") and f.hash is not None
    ]
    if len(recorded) != 1 or recorded[0].hash is None or recorded[0].hash.mode != "sha256":
        raise BindingMismatch("pysweph's install record has no SHA-256 for the swisseph module")
    actual = hashlib.sha256(Path(swe.__file__).read_bytes()).digest()
    if base64.urlsafe_b64encode(actual).rstrip(b"=").decode() != recorded[0].hash.value:
        raise BindingMismatch(f"{swe.__file__} is not the module pysweph {dist.version} installed")
    return f"pysweph {dist.version}"


def _pinned_pysweph_version() -> str:
    """The exact pysweph version in this project's own dependencies (pyproject.toml)."""
    for requirement in importlib.metadata.requires("astro-research") or ():
        if match := re.fullmatch(r"pysweph==([\w.]+)", requirement.replace(" ", "")):
            return match.group(1)
    raise BindingMismatch("the project must pin pysweph exactly (pysweph==X.Y.Z)")


def _verify_files(ephe_dir: Path, manifest: EphemerisManifest) -> None:
    if not ephe_dir.is_dir():
        raise EphemerisFileError(f"{ephe_dir} is not a folder; run scripts/fetch_ephemeris.py")
    expected = {f.name for f in manifest.files}
    # Dotfiles are ignored: Finder writes .DS_Store, and the fetch script stages downloads as
    # dotfiles. Swiss Ephemeris reads none of them.
    present = {p.name for p in ephe_dir.iterdir() if not p.name.startswith(".")}
    if unexpected := sorted(present - expected):
        names = ", ".join(unexpected)
        raise EphemerisFileError(f"{ephe_dir} holds files outside the manifest: {names}")
    if missing := sorted(expected - present):
        names = ", ".join(missing)
        raise EphemerisFileError(f"{ephe_dir} is missing {names}; run scripts/fetch_ephemeris.py")
    for entry in manifest.files:
        data = (ephe_dir / entry.name).read_bytes()
        if len(data) != entry.size or hashlib.sha256(data).hexdigest() != entry.sha256:
            raise EphemerisFileError(f"{entry.name} in {ephe_dir} does not match the manifest")


def _verify_loaded(ephe_dir: Path, manifest: EphemerisManifest) -> None:
    global _active_folder
    folder = ephe_dir.resolve()
    _active_folder = None  # every existing handle goes stale the moment the path moves
    swe.set_ephe_path(str(folder))
    # The Sun loads the planet file (file index 0) and the Moon the Moon file (index 1).
    for body, file_index in ((swe.SUN, 0), (swe.MOON, 1)):
        _position, retflag, serr = swe.calc_ut(_PROBE_JD_UT, body, swe.FLG_SWIEPH)
        if not retflag & swe.FLG_SWIEPH or serr:
            raise EphemerisNotLoaded(f"Swiss Ephemeris did not use its data files: {serr.strip()}")
        path, _start, _end, de_number = swe.get_current_file_data(file_index)
        if Path(path).resolve().parent != folder:
            raise EphemerisNotLoaded(f"loaded {path}, outside {folder}; is SE_EPHE_PATH set?")
        if de_number != manifest.de_number:
            raise EphemerisNotLoaded(f"{path} is DE{de_number}, not DE{manifest.de_number}")
    _verify_star_catalogue(folder / "sefstars.txt")
    # Swiss Ephemeris reads extra leap seconds from a seleapsec.txt in the data path at its first
    # UTC conversion in the process, and keeps them (observed). These are the engine's first
    # conversions, made once the path points at the verified folder, which can hold no such file.
    # Both also check the leap seconds Swiss Ephemeris holds, in case something converted earlier.
    _packaged_ut1()
    _model_scale_end()
    _active_folder = folder


class Ephemeris:
    """A verified Swiss Ephemeris environment; every calculation goes through one.

    Obtain it with `Ephemeris.open(ephe_dir)`, which runs `verify_ephemeris()`. Swiss Ephemeris
    has a single process-wide data path, so a handle refuses to calculate once anything has
    pointed that path elsewhere, or while SE_EPHE_PATH is set.
    """

    def __init__(self, folder: Path, provenance: EphemerisProvenance, token: object) -> None:
        if token is not _OPEN:
            raise TypeError("use Ephemeris.open(ephe_dir)")
        self._folder = folder
        self._provenance = provenance

    @classmethod
    def open(cls, ephe_dir: Path) -> "Ephemeris":
        provenance = verify_ephemeris(ephe_dir)
        return cls(ephe_dir.resolve(), provenance, _OPEN)

    @property
    def provenance(self) -> EphemerisProvenance:
        return self._provenance

    def time(self, instant: ResolvedInstant, time_scale: TimeScale) -> AstronomicalTime:
        self._require_active()
        utc = instant.utc
        seconds = utc.second + utc.microsecond / 1_000_000
        hours = utc.hour + utc.minute / 60 + seconds / 3600
        clock = swe.julday(utc.year, utc.month, utc.day, hours, swe.GREG_CAL)
        match time_scale:
            case TimeScale.CLOCK_AS_UT1:
                return _time_with_model_tt(
                    instant, time_scale, clock, clock, Ut1Basis.CLOCK_READING
                )
            case TimeScale.UT1_FROM_DELTA_T:
                end = _model_scale_end()
                if end is not None and utc.date() >= end:
                    raise _past_model_scale(end, f"{utc:%Y-%m-%d %H:%M:%S.%f} UTC")
                _, jd_ut1 = swe.utc_to_jd(
                    utc.year, utc.month, utc.day, utc.hour, utc.minute, seconds, swe.GREG_CAL
                )
                basis = Ut1Basis.CIVIL_TIME_AS_UT if clock < _JD_1972 else Ut1Basis.DELTA_T_MODEL
                return _time_with_model_tt(instant, time_scale, clock, jd_ut1, basis)
            case TimeScale.UT1_FROM_IERS:
                if clock < _JD_1972:
                    basis = Ut1Basis.CIVIL_TIME_AS_UT
                    return _time_with_model_tt(instant, time_scale, clock, clock, basis)
                table = _packaged_ut1()
                if utc > datetime.combine(table.last_day, datetime.min.time(), UTC):
                    raise _after_ut1_data(table, f"{utc:%Y-%m-%d %H:%M:%S.%f} UTC")
                jd_tt, _ = swe.utc_to_jd(
                    utc.year, utc.month, utc.day, utc.hour, utc.minute, seconds, swe.GREG_CAL
                )
                delta_t_s = table.delta_t_at_tt(jd_tt)
                # TT - UTC is 32.184 s plus a whole number of leap seconds (TAI - UTC).
                tai_minus_utc = round((jd_tt - clock) * 86400 - 32.184)
                return AstronomicalTime(
                    instant=instant,
                    time_scale=time_scale,
                    jd_ut1=jd_tt - delta_t_s / 86400,
                    jd_tt=jd_tt,
                    delta_t_s=delta_t_s,
                    ut1_minus_utc_s=32.184 + tai_minus_utc - delta_t_s,
                    ut1_basis=Ut1Basis.IERS_C04,
                )
            case _:
                assert_never(time_scale)

    def positions(self, time: AstronomicalTime, bodies: Iterable[Point] = tuple(Body)) -> Positions:
        """Positions at `time`'s TT of bodies and lunar points; duplicates are dropped.

        The default is the ten bodies; pass `ALL_POINTS` to include the lunar points.
        """
        self._require_active()
        computed = []
        for body in dict.fromkeys(bodies):
            ecliptic = _calc(time.jd_tt, body, _ECLIPTIC_FLAGS)
            equatorial = _calc(time.jd_tt, body, _EQUATORIAL_FLAGS)
            computed.append(
                BodyPosition(
                    body=body,
                    longitude=ecliptic[0],
                    latitude=ecliptic[1],
                    distance_au=ecliptic[2],
                    longitude_speed=ecliptic[3],
                    latitude_speed=ecliptic[4],
                    distance_speed=ecliptic[5],
                    right_ascension=equatorial[0],
                    declination=equatorial[1],
                    right_ascension_speed=equatorial[3],
                    declination_speed=equatorial[4],
                )
            )
        return Positions(
            time=time,
            ephemeris=self._provenance,
            conventions=POSITION_CONVENTIONS,
            bodies=tuple(computed),
        )

    def angles(self, time: AstronomicalTime, place: GeoPosition) -> Angles:
        """The chart's angles at `time`'s UT1 for `place`; refused at the geographic poles."""
        self._require_active()
        if abs(place.latitude) == 90.0:
            raise AnglesUndefined(f"the angles are undefined at latitude {place.latitude}")
        # The angles do not depend on the house system; Equal houses never fail at high
        # latitude, whereas Placidus and Koch raise beyond the polar circles (observed).
        _cusps, ascmc, _cusp_speeds, speeds = swe.houses_ex2(
            time.jd_ut1, place.latitude, place.longitude, b"E", swe.FLG_SWIEPH
        )
        true_obliquity = swe.calc(time.jd_tt, swe.ECL_NUT, 0)[0][0]
        return Angles(
            time=time,
            place=place,
            ephemeris=self._provenance,
            conventions=ANGLE_CONVENTIONS,
            armc=ascmc[2],
            armc_speed=speeds[2],
            ascendant=ascmc[0],
            ascendant_speed=speeds[0],
            midheaven=ascmc[1],
            midheaven_speed=speeds[1],
            descendant=(ascmc[0] + 180) % 360,
            imum_coeli=(ascmc[1] + 180) % 360,
            vertex=ascmc[3],
            vertex_speed=speeds[3],
            true_obliquity=true_obliquity,
            beyond_polar_circle=abs(place.latitude) > 90 - true_obliquity,
        )

    def time_from_tt(self, jd_tt: float, time_scale: TimeScale) -> AstronomicalTime:
        """The instant `jd_tt` (TT) as the AstronomicalTime of its UTC reading under `time_scale`.

        Under CLOCK_AS_UT1 the reading is UT1 (TT minus Swiss Ephemeris's delta-T), so it never
        falls in a leap second. Under the other two it is TT's own UTC, and an instant inside a
        leap second has no reading and is refused; UT1_FROM_IERS also refuses an instant after
        its table's last day, and reads one before 1972 as the clock convention does. The reading
        is rounded to the microsecond, so the result's jd_tt differs from `jd_tt` by up to 0.5 us
        plus Julian-date rounding (about 40 us).

        Under UT1_FROM_DELTA_T, a reading that doesn't convert back to `jd_tt` within 1 ms is
        refused: just before Swiss Ephemeris stops applying leap seconds, the reading it gives
        for a TT is a second early (from 2033-09-16 15:59:27 UTC, measured).
        """
        self._require_active()
        match time_scale:
            case TimeScale.CLOCK_AS_UT1:
                reading = _reading_of_ut1(_ut1_from_tt(jd_tt))
            case TimeScale.UT1_FROM_DELTA_T:
                parts = swe.jdut1_to_utc(_ut1_from_tt(jd_tt), swe.GREG_CAL)
                reading = _utc_reading(jd_tt, *parts)
            case TimeScale.UT1_FROM_IERS:
                table = _packaged_ut1()
                if jd_tt < table.tt[0]:
                    reading = _reading_of_ut1(_ut1_from_tt(jd_tt))
                elif jd_tt > table.tt[-1]:
                    raise _after_ut1_data(table, f"JD {jd_tt} TT")
                else:
                    reading = _utc_reading(jd_tt, *swe.jdet_to_utc(jd_tt, swe.GREG_CAL))
            case _:
                assert_never(time_scale)
        time = self.time(resolve(UtcTime(utc=reading), _NOWHERE), time_scale)
        if time_scale is TimeScale.UT1_FROM_DELTA_T and abs(time.jd_tt - jd_tt) > _ROUND_TRIP_DAYS:
            end = _model_scale_end()
            given = f"{reading:%Y-%m-%d %H:%M:%S} UTC"
            raise _past_model_scale(end, f"JD {jd_tt} TT ({given}, which doesn't convert back)")
        return time

    def eclipse_search(
        self, kind: EclipseKind, start_jd_ut1: float, *, backward: bool
    ) -> EclipseCandidate:
        """Swiss Ephemeris's next eclipse of `kind` after (or before) `start_jd_ut1`, any type.

        Swiss Ephemeris falls back to its Moshier model outside the data files without saying so
        (observed), so callers must check the eclipse's instant against the files.
        """
        self._require_active()
        if kind is EclipseKind.SOLAR:
            flags, tret = swe.sol_eclipse_when_glob(start_jd_ut1, swe.FLG_SWIEPH, 0, backward)
            central: bool | None = _one_flag(
                flags, {swe.ECL_CENTRAL: True, swe.ECL_NONCENTRAL: False}
            )
            eclipse_type = _one_flag(flags, _SOLAR_TYPE_FLAGS)
        else:
            flags, tret = swe.lun_eclipse_when(start_jd_ut1, swe.FLG_SWIEPH, 0, backward)
            central = None
            eclipse_type = _one_flag(flags, _LUNAR_TYPE_FLAGS)
        estimate_ut1 = tret[0]
        return EclipseCandidate(
            kind=kind,
            type=eclipse_type,
            central=central,
            estimate_jd_ut1=estimate_ut1,
            estimate_jd_tt=estimate_ut1 + _delta_t_days(estimate_ut1),
        )

    def eclipse_attributes(self, kind: EclipseKind, time: AstronomicalTime) -> EclipseAttributes:
        """Magnitudes, saros and (solar) the place of greatest eclipse, at `time`."""
        self._require_active()
        if kind is EclipseKind.SOLAR:
            with _swiss_ephemeris_delta_t(_iers_delta_t_s(time)):
                _, where, attr = swe.sol_eclipse_where(time.jd_ut1, swe.FLG_SWIEPH)
            return EclipseAttributes(
                magnitude=attr[8],  # NASA's definition, per the Swiss Ephemeris manual
                penumbral_magnitude=None,
                saros_series=_saros(attr[9]),
                saros_member=_saros(attr[10]),
                place_of_greatest=GeoPosition(latitude=where[1], longitude=where[0]),
            )
        with _swiss_ephemeris_delta_t(_iers_delta_t_s(time)):
            _, attr = swe.lun_eclipse_how(time.jd_ut1, (0.0, 0.0, 0.0), swe.FLG_SWIEPH)
        return EclipseAttributes(
            magnitude=attr[0],  # umbral
            penumbral_magnitude=attr[1],
            saros_series=_saros(attr[9]),
            saros_member=_saros(attr[10]),
            place_of_greatest=None,
        )

    def apparent_vectors(
        self, jd_tt: float
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """Geocentric apparent equatorial positions of the Sun and Moon, in AU; refused outside
        the data files."""
        self._require_active()
        sun = _calc(jd_tt, Body.SUN, _VECTOR_FLAGS)
        moon = _calc(jd_tt, Body.MOON, _VECTOR_FLAGS)
        return (sun[0], sun[1], sun[2]), (moon[0], moon[1], moon[2])

    def time_from_ut1(self, jd_ut1: float, time_scale: TimeScale) -> AstronomicalTime:
        """`time_from_tt` for a UT1 Julian date, as Swiss Ephemeris's rise/set functions return."""
        self._require_active()
        if time_scale is TimeScale.UT1_FROM_IERS:
            table = _packaged_ut1()
            # 1972 began 0.045 s earlier in UT1 than the civil reading did: that overlap is IERS's.
            if jd_ut1 >= table.first_ut1:
                return self.time_from_tt(table.tt_from_ut1(jd_ut1), time_scale)
        return self.time_from_tt(jd_ut1 + _delta_t_days(jd_ut1), time_scale)

    def star_name(self, star: FixedStar) -> str:
        """The catalogue's name for `star`, e.g. "Regulus,alLeo"."""
        self._require_active()
        try:
            _position, name, _flag = swe.fixstar2_ut(star.designation, _PROBE_JD_UT, swe.FLG_SWIEPH)
        except swe.Error as error:
            raise StarNotFound(f"{star.designation}: {error}") from error
        found: str = name
        return found

    def rise_trans(
        self,
        point: Body | FixedStar,
        event: HorizonEvent,
        start_jd_ut1: float,
        place: GeoPosition,
        horizon: HorizonConvention,
        time_scale: TimeScale,
    ) -> float | None:
        """The UT1 Julian date of the next `event` after `start_jd_ut1`, or None when Swiss
        Ephemeris reports no rising or setting (circumpolar or never rising, it cannot say which).

        Topocentric and in mundo (Swiss Ephemeris's default positions). Swiss Ephemeris falls back
        to its Moshier model outside the data files without saying so, so callers check dates;
        resolve a star with `star_name` first, which refuses an unknown one.

        Swiss Ephemeris finds the event's TT with its own delta-T. Under UT1_FROM_IERS it is given
        IERS's at the search's start instead: delta-T changes by under 5 ms over the day a search
        can cover (since 1972, measured), and a search started just before the event, as parans
        refine theirs, has it to microseconds.
        """
        self._require_active()
        rsmi = _RISE_TRANS_EVENT[event]
        if event in (HorizonEvent.RISE, HorizonEvent.SET):
            rsmi |= _RISE_TRANS_DISC[horizon.disc]
        if horizon.refraction is None:
            rsmi |= swe.BIT_NO_REFRACTION
            pressure, temperature = 0.0, 0.0  # unused without refraction
        else:
            pressure = horizon.refraction.pressure_hpa
            temperature = horizon.refraction.temperature_c
        target = point.designation if isinstance(point, FixedStar) else _SE_POINT[point]
        geopos = (place.longitude, place.latitude, place.elevation_m)
        delta_t_s = None
        if time_scale is TimeScale.UT1_FROM_IERS:
            delta_t_s = _iers_delta_t_at_ut1(start_jd_ut1)
        with _swiss_ephemeris_delta_t(delta_t_s):
            code, tret = swe.rise_trans(
                start_jd_ut1, target, rsmi, geopos, pressure, temperature, swe.FLG_SWIEPH
            )
        if code == -2:
            return None
        found: float = tret[0]
        return found

    def apparent_longitudes(self, jd_tt: float) -> tuple[float, float, float, float]:
        """Apparent ecliptic longitudes and speeds: (Sun, its speed, Moon, its speed)."""
        self._require_active()
        sun = _calc(jd_tt, Body.SUN, _ECLIPTIC_FLAGS)
        moon = _calc(jd_tt, Body.MOON, _ECLIPTIC_FLAGS)
        return sun[0], sun[3], moon[0], moon[3]

    def _require_active(self) -> None:
        if _active_folder != self._folder:
            raise StaleEphemeris(
                f"the Swiss Ephemeris data path no longer points at {self._folder}; reopen it"
            )
        if "SE_EPHE_PATH" in os.environ:
            raise EphemerisNotLoaded("SE_EPHE_PATH is set and overrides the verified folder")


def _calc(jd_tt: float, body: Point, flags: int) -> tuple[float, ...]:
    position, retflag, serr = swe.calc(jd_tt, _SE_POINT[body], flags)
    if not retflag & swe.FLG_SWIEPH:
        # Swiss Ephemeris would quietly continue with its Moshier model.
        raise DateOutsideEphemeris(f"{body.value} at JD {jd_tt} TT: {serr.strip()}")
    if serr:
        raise EphemerisNotLoaded(f"{body.value} at JD {jd_tt} TT: {serr.strip()}")
    return tuple(position)


def _delta_t_days(jd_ut1: float) -> float:
    delta_t: float
    delta_t, serr = swe.deltat_ex(jd_ut1, swe.FLG_SWIEPH)
    if serr:
        raise EphemerisNotLoaded(f"delta-T: {serr.strip()}")
    return delta_t


def _ut1_from_tt(jd_tt: float) -> float:
    """UT1 such that UT1 + delta-T(UT1) = TT; delta-T changes slowly, so three steps converge."""
    jd_ut1 = jd_tt
    for _ in range(3):
        jd_ut1 = jd_tt - _delta_t_days(jd_ut1)
    return jd_ut1


def _time_with_model_tt(
    instant: ResolvedInstant, time_scale: TimeScale, clock: float, jd_ut1: float, basis: Ut1Basis
) -> AstronomicalTime:
    """The time whose UT1 is `jd_ut1`, with TT from Swiss Ephemeris's delta-T."""
    # TT exactly as calc_ut derives it from UT1, i.e. with delta-T for the loaded DE.
    # utc_to_jd's own TT uses DE431's value, which differs before 1955 (0.30 s at 1800).
    # deltat_ex follows the tidal term of whichever files Swiss Ephemeris has opened, flag or
    # not (observed for 1800-06-01: 18.4180 s before any file is open, 18.7137 s after).
    # A handle exists only after the startup probe opened the pinned files, so this is the
    # DE441 value.
    delta_t = _delta_t_days(jd_ut1)
    return AstronomicalTime(
        instant=instant,
        time_scale=time_scale,
        jd_ut1=jd_ut1,
        jd_tt=jd_ut1 + delta_t,
        delta_t_s=delta_t * 86400,
        ut1_minus_utc_s=(jd_ut1 - clock) * 86400,
        ut1_basis=basis,
    )


def _reading_of_ut1(jd_ut1: float) -> datetime:
    """The UTC reading that the clock convention (or civil time before 1972) takes as UT1."""
    year, month, day, hours = swe.revjul(jd_ut1, swe.GREG_CAL)
    return datetime(year, month, day, tzinfo=UTC) + timedelta(hours=hours)


def _utc_reading(
    jd_tt: float, year: int, month: int, day: int, hour: int, minute: int, seconds: float
) -> datetime:
    """A UTC reading from Swiss Ephemeris's parts; refused inside a leap second."""
    if seconds >= 60:
        raise InstantInLeapSecond(
            f"JD {jd_tt} TT falls in the leap second {year}-{month:02d}-{day:02d} "
            f"{hour:02d}:{minute:02d}:{seconds:.6f} UTC"
        )
    return datetime(year, month, day, hour, minute, tzinfo=UTC) + timedelta(seconds=seconds)


@functools.cache
def _model_scale_end() -> date | None:
    """The first day Swiss Ephemeris's utc_to_jd takes a reading as UT1 instead of applying leap
    seconds, or None. It does so once its delta-T model puts UT1 - UTC past -1 s, from then on
    (2033-09-17, observed; checked to 2400). There it returns the day's 0h as UT1 exactly, which
    the model's own UT1 isn't on any day before (observed). It can't come before the IERS table
    ends, whose days all have a whole leap-second count.

    Up to that day, Swiss Ephemeris's TAI - UTC must stay as it was on the table's last day. The
    pinned binding's own table ends at 2016-12-31, and later leap seconds can only come from a
    seleapsec.txt (swedate.c:303, 311-344, read). After that day no scale uses its leap seconds."""
    table = _packaged_ut1()
    day = table.last_day
    expected: float | None = None
    while day < _SCAN_END:
        jd_tt, jd_ut1 = swe.utc_to_jd(day.year, day.month, day.day, 0, 0, 0.0, swe.GREG_CAL)
        midnight = swe.julday(day.year, day.month, day.day, 0.0, swe.GREG_CAL)
        if jd_ut1 == midnight:
            return day
        tai_minus_utc = (jd_tt - midnight) * 86400 - 32.184
        expected = tai_minus_utc if expected is None else expected
        if abs(tai_minus_utc - expected) > 1e-3:
            raise EphemerisNotLoaded(_leap_seconds_differ(day))
        day += timedelta(days=1)
    return None


def _leap_seconds_differ(day: date) -> str:
    return (
        f"Swiss Ephemeris's leap seconds differ from the pinned ones at {day}: it reads a "
        "seleapsec.txt from the data path at its first UTC conversion in the process and keeps "
        "it; restart without one in the path that was set then"
    )


def _past_model_scale(end: date | None, what: str) -> DateOutsideLeapSeconds:
    return DateOutsideLeapSeconds(
        f"{what} cannot be read under UT1_FROM_DELTA_T: from {end}, Swiss Ephemeris's delta-T "
        "model puts UT1 - UTC past -1 s, and it takes readings as UT1 instead of applying leap "
        "seconds (conversions from TT go wrong from the afternoon before). That is the clock "
        "convention: name clock_as_ut1"
    )


def _after_ut1_data(table: Ut1Table, what: str) -> DateOutsideUt1Data:
    return DateOutsideUt1Data(
        f"{what} is after {table.last_day}, the packaged IERS UT1 table's last day; name "
        "another time scale"
    )


def _iers_delta_t_s(time: AstronomicalTime) -> float | None:
    """`time`'s delta-T when it came from IERS; None when it is Swiss Ephemeris's own."""
    return time.delta_t_s if time.ut1_basis is Ut1Basis.IERS_C04 else None


def _iers_delta_t_at_ut1(jd_ut1: float) -> float | None:
    """IERS's delta-T at a UT1 Julian date; None before 1972, where the model applies."""
    table = _packaged_ut1()
    if jd_ut1 < table.first_ut1:
        return None
    jd_tt = table.tt_from_ut1(jd_ut1)
    if jd_tt > table.tt[-1]:
        raise _after_ut1_data(table, f"JD {jd_ut1} UT1")
    return table.delta_t_at_tt(jd_tt)


@contextlib.contextmanager
def _swiss_ephemeris_delta_t(delta_t_s: float | None) -> Iterator[None]:
    """Swiss Ephemeris converts UT to TT with its own delta-T model, a process-wide setting.
    Given another delta-T, it uses that within the block and its model again after, whatever
    happens inside."""
    if delta_t_s is None:
        yield
        return
    swe.set_delta_t_userdef(delta_t_s / 86400)
    try:
        yield
    finally:
        swe.set_delta_t_userdef(swe.DELTAT_AUTOMATIC)


def _one_flag[T](flags: int, table: dict[int, T]) -> T:
    """The one entry of `table` whose bit is set in `flags`; anything else is unexpected."""
    found = [value for bit, value in table.items() if flags & bit]
    if len(found) != 1:
        raise EphemerisNotLoaded(f"unexpected eclipse flags {flags:#x}")
    return found[0]


def _saros(value: float) -> int | None:
    return None if value == _NO_SAROS else int(value)


def _verify_star_catalogue(catalogue: Path) -> None:
    """Swiss Ephemeris must hold the pinned catalogue in memory, not one read earlier."""
    expected = _catalogue_position(catalogue, _STAR_PROBE)
    position, _name, _flag = swe.fixstar2("," + _STAR_PROBE, 2451545.0, _STAR_CATALOGUE_FLAGS)
    ra, dec = position[0], position[1]
    if max(abs(ra - expected[0]), abs(dec - expected[1])) > _STAR_PROBE_TOLERANCE_DEG:
        raise EphemerisNotLoaded(
            f"Swiss Ephemeris holds a different star catalogue from {catalogue}: it keeps the "
            "first one it reads for the whole process; restart with the pinned folder"
        )


def _catalogue_position(catalogue: Path, designation: str) -> tuple[float, float]:
    """ICRS right ascension and declination (degrees) of the first record for `designation`."""
    for line in catalogue.read_text(encoding="latin-1").splitlines():
        fields = [field.strip() for field in line.split(",")]
        if line.startswith("#") or len(fields) < 9 or fields[1] != designation:
            continue
        ra = (float(fields[3]) + float(fields[4]) / 60 + float(fields[5]) / 3600) * 15
        sign = -1.0 if fields[6].startswith("-") else 1.0
        dec = sign * (abs(float(fields[6])) + float(fields[7]) / 60 + float(fields[8]) / 3600)
        return ra, dec
    raise EphemerisFileError(f"{catalogue} has no record for {designation}")
