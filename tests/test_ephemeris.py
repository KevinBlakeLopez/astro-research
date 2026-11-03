"""Swiss Ephemeris environment verification: the engine's one-time fail-closed check.

Needs the pinned data files in ephe/ (`uv run python scripts/fetch_ephemeris.py`). These tests
fail rather than skip when the files are missing, so a run cannot pass without checking them.
"""

import dataclasses
import importlib.metadata
import re
import shutil
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
    assert {f.name for f in manifest.files} == {"sepl_18.se1", "semo_18.se1"}
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
