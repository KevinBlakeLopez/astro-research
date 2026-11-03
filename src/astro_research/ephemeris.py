"""Swiss Ephemeris adapter: the only module that imports `swisseph`.

It starts with the one-time environment check that every calculation depends on. The engine
fails closed (decision D1), so `verify_ephemeris()` confirms, in order:

1. The `swisseph` module is the pysweph build the project pins, byte for byte as installed.
2. The data folder holds exactly the manifest's files, each matching its SHA-256.
3. Swiss Ephemeris, asked for its full-precision ephemeris, really loaded those files: the
   returned flag says so, and each loaded file lies inside the folder with the manifest's DE
   number. That catches an SE_EPHE_PATH override, which takes priority over set_ephe_path.

The ephemeris path is process-wide state in Swiss Ephemeris; this module owns it.
"""

import base64
import hashlib
import importlib.metadata
import importlib.resources
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import swisseph as swe

_PROBE_JD_UT = 2451545.0  # 2000-01-01 12:00 UT, inside the pinned files


class EphemerisEnvironmentError(RuntimeError):
    """The calculation environment is not the pinned one; nothing may be calculated."""


class BindingMismatch(EphemerisEnvironmentError):
    """The `swisseph` module is not the pysweph build the project pins."""


class EphemerisFileError(EphemerisEnvironmentError):
    """The data folder does not hold exactly the manifest's files."""


class EphemerisNotLoaded(EphemerisEnvironmentError):
    """Swiss Ephemeris did not load the verified files at full precision."""


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
    folder = ephe_dir.resolve()
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
