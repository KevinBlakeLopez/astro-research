"""Swiss Ephemeris environment verification: the engine's one-time fail-closed check.

Needs the pinned data files in ephe/ (`uv run python scripts/fetch_ephemeris.py`). These tests
fail rather than skip when the files are missing, so a run cannot pass without checking them.
"""

import dataclasses
import importlib.metadata
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import swisseph

from astro_research import ephemeris
from astro_research.ephemeris import (
    BindingMismatch,
    EphemerisFileError,
    EphemerisNotLoaded,
    load_manifest,
    verify_ephemeris,
)


@pytest.fixture
def ephe_copy(fetched_ephe: Path, tmp_path: Path) -> Path:
    """A private copy of the pinned files that a test may damage."""
    copy = tmp_path / "ephe"
    copy.mkdir()
    for entry in load_manifest().files:
        shutil.copy2(fetched_ephe / entry.name, copy / entry.name)
    return copy


# --- Manifest -----------------------------------------------------------------------------


def test_manifest_pins_de441_files_by_hash() -> None:
    manifest = load_manifest()
    assert manifest.de_number == 441
    assert {f.name for f in manifest.files} == {"sepl_18.se1", "semo_18.se1", "sefstars.txt"}
    for entry in manifest.files:
        assert re.fullmatch(r"[0-9a-f]{64}", entry.sha256)
        url = manifest.url(entry.name)
        assert manifest.source_commit in url
        assert url.endswith("/ephe/" + entry.name)


# --- A good environment -------------------------------------------------------------------


def test_verified_environment_reports_its_provenance(fetched_ephe: Path) -> None:
    provenance = verify_ephemeris(fetched_ephe)
    manifest = load_manifest()
    assert provenance.de_number == 441
    assert provenance.binding == f"pysweph {importlib.metadata.version('pysweph')}"
    # The C version string is identical across the 2022-2026 data builds, hence the hashes.
    assert provenance.swisseph_version == "2.10.03"
    assert provenance.source_commit == manifest.source_commit
    assert dict(provenance.file_sha256) == {f.name: f.sha256 for f in manifest.files}


def test_the_verified_folder_serves_the_pinned_star_catalogue(fetched_ephe: Path) -> None:
    # The catalogue is read from the verified folder: its hash is pinned, and Swiss Ephemeris
    # finds a star in it by designation.
    entry = next(f for f in load_manifest().files if f.name == "sefstars.txt")
    assert entry.sha256 == "18b0dcafbe5b7240773daba2c038a325f5b3fc4163f61e0a7f4e92abd4f517c6"
    verify_ephemeris(fetched_ephe)
    _position, name, _flag = swisseph.fixstar2_ut(",alLeo", 2451545.0, swisseph.FLG_SWIEPH)
    assert name == "Regulus,alLeo"


def test_a_star_catalogue_read_earlier_in_the_process_is_detected(
    fetched_ephe: Path, tmp_path: Path
) -> None:
    # Swiss Ephemeris keeps the first star catalogue it reads for the whole process, whatever the
    # data path says later (observed). A fresh process reads an altered catalogue first; the
    # pinned folder must then be refused.
    altered = tmp_path / "altered"
    shutil.copytree(fetched_ephe, altered)
    catalogue = altered / "sefstars.txt"
    text = catalogue.read_text(encoding="latin-1")
    assert text.count(",alLeo,ICRS,10,08,22.31099") >= 1
    catalogue.write_text(
        text.replace(",alLeo,ICRS,10,08,22.31099", ",alLeo,ICRS,11,08,22.31099"), encoding="latin-1"
    )
    script = (
        "import sys, swisseph\n"
        "from pathlib import Path\n"
        "from astro_research.ephemeris import EphemerisNotLoaded, verify_ephemeris\n"
        "swisseph.set_ephe_path(sys.argv[1])\n"
        "swisseph.fixstar2_ut(',alLeo', 2451545.0, swisseph.FLG_SWIEPH)\n"
        "try:\n"
        "    verify_ephemeris(Path(sys.argv[2]))\n"
        "except EphemerisNotLoaded as error:\n"
        "    print('refused:', error)\n"
        "else:\n"
        "    print('accepted')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(altered), str(fetched_ephe)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.startswith("refused:"), result.stdout + result.stderr
    assert "star catalogue" in result.stdout


def test_copy_of_the_pinned_files_verifies(ephe_copy: Path) -> None:
    assert verify_ephemeris(ephe_copy).de_number == 441


def test_dotfiles_are_ignored(ephe_copy: Path) -> None:
    (ephe_copy / ".DS_Store").write_bytes(b"\0")  # Finder creates these
    assert verify_ephemeris(ephe_copy).de_number == 441


# --- Data files ---------------------------------------------------------------------------


def test_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(EphemerisFileError):
        verify_ephemeris(tmp_path / "nowhere")


def test_missing_file(ephe_copy: Path) -> None:
    (ephe_copy / "semo_18.se1").unlink()
    with pytest.raises(EphemerisFileError, match=re.escape("semo_18.se1")):
        verify_ephemeris(ephe_copy)


def test_altered_file(ephe_copy: Path) -> None:
    target = ephe_copy / "sepl_18.se1"
    data = bytearray(target.read_bytes())
    data[len(data) // 2] ^= 0x01  # one bit; the size is unchanged
    target.write_bytes(bytes(data))
    with pytest.raises(EphemerisFileError, match=re.escape("sepl_18.se1")):
        verify_ephemeris(ephe_copy)


@pytest.mark.parametrize("stray", ["swe_deltat.txt", "seleapsec.txt", "sepl_12.se1"])
def test_unexpected_file(ephe_copy: Path, stray: str) -> None:
    # Swiss Ephemeris picks up delta-T and leap-second tables from its path when present.
    (ephe_copy / stray).write_text("not part of the manifest\n")
    with pytest.raises(EphemerisFileError, match=re.escape(stray)):
        verify_ephemeris(ephe_copy)


# --- What Swiss Ephemeris actually loaded -------------------------------------------------
# SE_EPHE_PATH takes priority over set_ephe_path and is read on every call (observed).


def test_override_to_an_empty_folder_is_detected(
    ephe_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Swiss Ephemeris silently falls back to its Moshier model here.
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("SE_EPHE_PATH", str(empty))
    with pytest.raises(EphemerisNotLoaded):
        verify_ephemeris(ephe_copy)


def test_override_to_valid_files_elsewhere_is_detected(
    ephe_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Same files, other folder: full precision and DE441, so only the loaded path betrays it.
    elsewhere = tmp_path / "elsewhere"
    shutil.copytree(ephe_copy, elsewhere)
    monkeypatch.setenv("SE_EPHE_PATH", str(elsewhere))
    with pytest.raises(EphemerisNotLoaded, match="elsewhere"):
        verify_ephemeris(ephe_copy)


# --- Binding ------------------------------------------------------------------------------


def test_binding_version_must_match_the_project_pin(
    fetched_ephe: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(importlib.metadata, "requires", lambda name: ["pysweph==9.9.9"])
    with pytest.raises(BindingMismatch):
        verify_ephemeris(fetched_ephe)


def test_replaced_binding_module_is_detected(
    fetched_ephe: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # pyswisseph installs the same module file and silently overwrites pysweph's. The file on
    # disk then no longer matches the hash pysweph recorded when it was installed.
    impostor = tmp_path / Path(swisseph.__file__).name
    impostor.write_bytes(b"not the pysweph build")
    monkeypatch.setattr(swisseph, "__file__", str(impostor))
    with pytest.raises(BindingMismatch):
        verify_ephemeris(fetched_ephe)


# --- Checks that overlap with others in practice --------------------------------------------
# Tested directly so that a refactor cannot remove them unnoticed.


def test_manifest_de_number_must_match_the_loaded_files(
    fetched_ephe: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Guards against changing the manifest's DE number without the files, or the reverse.
    claims_de431 = dataclasses.replace(load_manifest(), de_number=431)
    monkeypatch.setattr(ephemeris, "load_manifest", lambda: claims_de431)
    with pytest.raises(EphemerisNotLoaded, match="DE441"):
        verify_ephemeris(fetched_ephe)


def test_fallback_flag_is_checked(fetched_ephe: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A real fallback also shows up as a path outside the folder; here only the flag is wrong.
    real_calc_ut = swisseph.calc_ut

    def without_full_precision(jd: float, body: int, flags: int) -> tuple[object, int, str]:
        position, retflag, serr = real_calc_ut(jd, body, flags)
        return position, retflag & ~swisseph.FLG_SWIEPH, serr

    monkeypatch.setattr(swisseph, "calc_ut", without_full_precision)
    with pytest.raises(EphemerisNotLoaded):
        verify_ephemeris(fetched_ephe)
