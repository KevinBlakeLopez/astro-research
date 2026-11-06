"""Swiss Ephemeris adapter: the only module that imports `swisseph`.

It starts with the one-time environment check that every calculation depends on. The engine
fails closed (decision D1), so `verify_ephemeris()` confirms, in order:

1. The `swisseph` module is the pysweph build the project pins, byte for byte as installed.
2. The data folder holds exactly the manifest's files, each matching its SHA-256.
3. Swiss Ephemeris, asked for its full-precision ephemeris, really loaded those files: the
   returned flag says so, and each loaded file lies inside the folder with the manifest's DE
   number. That catches an SE_EPHE_PATH override, which takes priority over set_ephe_path.

The ephemeris path is process-wide state in Swiss Ephemeris; this module owns it. Calculations
go through an `Ephemeris` handle, which only `Ephemeris.open()` creates (after the check) and
which refuses to calculate once the path has moved elsewhere.
"""

import base64
import hashlib
import importlib.metadata
import importlib.resources
import os
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import assert_never

import swisseph as swe

from astro_research.angles import ANGLE_CONVENTIONS, Angles, AnglesUndefined
from astro_research.astronomical_time import AstronomicalTime, InstantInLeapSecond, TimeScale
from astro_research.civil_time import ResolvedInstant, UtcTime, resolve
from astro_research.eclipses import (
    EclipseAttributes,
    EclipseCandidate,
    EclipseKind,
    EclipseType,
)
from astro_research.geo import GeoPosition
from astro_research.positions import (
    POSITION_CONVENTIONS,
    Body,
    BodyPosition,
    LunarPoint,
    Point,
    Positions,
)

_PROBE_JD_UT = 2451545.0  # 2000-01-01 12:00 UT, inside the pinned files
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


@dataclass(frozen=True, kw_only=True)
class ManifestFile:
    name: str
    covers: str
    size: int
    sha256: str
    header: str


@dataclass(frozen=True, kw_only=True)
class EphemerisManifest:
    de_number: int
    source_commit: str
    url_template: str
    files: tuple[ManifestFile, ...]

    def url(self, name: str) -> str:
        return self.url_template.format(commit=self.source_commit, name=name)


@dataclass(frozen=True, kw_only=True)
class EphemerisProvenance:
    binding: str  # e.g. "pysweph 2.10.3.6"
    swisseph_version: str  # C library string; unchanged across the 2022-2026 data builds
    de_number: int  # as reported by Swiss Ephemeris for the loaded files
    source_commit: str
    file_sha256: tuple[tuple[str, str], ...]  # (file name, verified SHA-256)


def load_manifest() -> EphemerisManifest:
    resource = importlib.resources.files("astro_research").joinpath("ephemeris_manifest.toml")
    raw = tomllib.loads(resource.read_text())
    return EphemerisManifest(
        de_number=raw["de_number"],
        source_commit=raw["source_commit"],
        url_template=raw["url_template"],
        files=tuple(ManifestFile(**entry) for entry in raw["files"]),
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
    )


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
                jd_ut1 = clock
            case TimeScale.UT1_FROM_DELTA_T:
                _, jd_ut1 = swe.utc_to_jd(
                    utc.year, utc.month, utc.day, utc.hour, utc.minute, seconds, swe.GREG_CAL
                )
            case _:
                assert_never(time_scale)
        # TT exactly as calc_ut derives it from UT1, i.e. with delta-T for the loaded DE.
        # utc_to_jd's own TT uses DE431's value, which differs before 1955 (0.30 s at 1800).
        # deltat_ex follows the tidal term of whichever files Swiss Ephemeris has opened, flag or
        # not (observed for 1800-06-01: 18.4180 s before any file is open, 18.7137 s after).
        # A handle exists only after the startup probe opened the pinned files, so this is the
        # DE441 value.
        delta_t, serr = swe.deltat_ex(jd_ut1, swe.FLG_SWIEPH)
        if serr:
            raise EphemerisNotLoaded(f"delta-T: {serr.strip()}")
        return AstronomicalTime(
            instant=instant,
            time_scale=time_scale,
            jd_ut1=jd_ut1,
            jd_tt=jd_ut1 + delta_t,
            delta_t_s=delta_t * 86400,
            ut1_minus_utc_s=(jd_ut1 - clock) * 86400,
        )

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

        UT1 is TT minus Swiss Ephemeris's delta-T, as in `time()`. Under CLOCK_AS_UT1 the UTC
        reading is UT1 itself, so it never falls in a leap second; under UT1_FROM_DELTA_T an
        instant inside a leap second has no reading and is refused. The reading is rounded to the
        microsecond, so the result's jd_tt differs from `jd_tt` by up to 0.5 us plus Julian-date
        rounding (about 40 us).
        """
        self._require_active()
        jd_ut1 = _ut1_from_tt(jd_tt)
        match time_scale:
            case TimeScale.CLOCK_AS_UT1:
                year, month, day, hours = swe.revjul(jd_ut1, swe.GREG_CAL)
                reading = datetime(year, month, day, tzinfo=UTC) + timedelta(hours=hours)
            case TimeScale.UT1_FROM_DELTA_T:
                year, month, day, hour, minute, seconds = swe.jdut1_to_utc(jd_ut1, swe.GREG_CAL)
                if seconds >= 60:
                    raise InstantInLeapSecond(
                        f"JD {jd_tt} TT falls in the leap second {year}-{month:02d}-{day:02d} "
                        f"{hour:02d}:{minute:02d}:{seconds:.6f} UTC"
                    )
                start = datetime(year, month, day, hour, minute, tzinfo=UTC)
                reading = start + timedelta(seconds=seconds)
            case _:
                assert_never(time_scale)
        return self.time(resolve(UtcTime(utc=reading), _NOWHERE), time_scale)

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

    def eclipse_attributes(self, kind: EclipseKind, jd_tt: float) -> EclipseAttributes:
        """Magnitudes, saros and (solar) the place of greatest eclipse, at `jd_tt`."""
        self._require_active()
        jd_ut1 = _ut1_from_tt(jd_tt)
        if kind is EclipseKind.SOLAR:
            _, where, attr = swe.sol_eclipse_where(jd_ut1, swe.FLG_SWIEPH)
            return EclipseAttributes(
                magnitude=attr[8],  # NASA's definition, per the Swiss Ephemeris manual
                penumbral_magnitude=None,
                saros_series=_saros(attr[9]),
                saros_member=_saros(attr[10]),
                place_of_greatest=GeoPosition(latitude=where[1], longitude=where[0]),
            )
        _, attr = swe.lun_eclipse_how(jd_ut1, (0.0, 0.0, 0.0), swe.FLG_SWIEPH)
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


def _one_flag[T](flags: int, table: dict[int, T]) -> T:
    """The one entry of `table` whose bit is set in `flags`; anything else is unexpected."""
    found = [value for bit, value in table.items() if flags & bit]
    if len(found) != 1:
        raise EphemerisNotLoaded(f"unexpected eclipse flags {flags:#x}")
    return found[0]


def _saros(value: float) -> int | None:
    return None if value == _NO_SAROS else int(value)
