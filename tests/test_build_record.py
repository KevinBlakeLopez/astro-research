"""The binding checked when a Mac app is built, for an app whose signing changes the module.

The engine checks that swisseph's module file is byte for byte the one pysweph recorded when it
was installed, which catches pyswisseph silently overwriting it. Signing a Mac app re-signs the
module, which changes its bytes (observed with Briefcase: 772,864 bytes became 786,448), so that
check can't pass inside a signed app. It's made when the app is built instead:

- `write_build_record(path)` runs the byte check, after the app's packages are installed and
  before they're signed, and writes a record of what matched: the pysweph version, the module's
  file name and its SHA-256. If the module doesn't match, it writes nothing.
  `python -m astro_research.build_record PATH` does the same from the command line.
- `use_build_record(path)`, called by the app's launcher, reads the record and checks it at
  once: the pinned pysweph version, this module's file name, and the SHA-256 pysweph recorded.
  From then on, a module whose bytes have changed is accepted, since the build checked it and the
  app's signature covers it. The binding is still "pysweph <version>", so calculation keys don't
  change.

A record that doesn't fit is refused with BindingMismatch, and nothing is relaxed. Without a
record, a replaced module is refused as before.
"""

import base64
import hashlib
import importlib.metadata
import re
import tomllib
from pathlib import Path

import pytest
import swisseph

from astro_research import build_record, ephemeris
from astro_research.ephemeris import (
    BindingMismatch,
    use_build_record,
    verify_ephemeris,
    write_build_record,
)

VERSION = importlib.metadata.version("pysweph")
MODULE = Path(swisseph.__file__).name


@pytest.fixture(autouse=True)
def no_build_record_in_use(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts without a record in use, and leaves none behind."""
    monkeypatch.setattr(ephemeris, "_build_record", None)


def sha256_hex(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recorded_by_pysweph() -> str:
    """The module's SHA-256 in pysweph's install record, as hex."""
    (entry,) = [
        f
        for f in importlib.metadata.distribution("pysweph").files or ()
        if f.name == MODULE and f.hash is not None
    ]
    assert entry.hash is not None
    value = entry.hash.value
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).hex()


def re_signed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stand in for the module after signing: same name, other bytes."""
    module = tmp_path / "signed" / MODULE
    module.parent.mkdir()
    module.write_bytes(Path(swisseph.__file__).read_bytes() + b"\x00 a signature")
    monkeypatch.setattr(swisseph, "__file__", str(module))
    return module


def record_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "binding-record.toml"
    path.write_text(text)
    return path


# --- Writing a record -----------------------------------------------------------------------


def test_a_record_holds_the_version_the_module_and_its_sha256(tmp_path: Path) -> None:
    path = tmp_path / "binding-record.toml"
    write_build_record(path)
    record = tomllib.loads(path.read_text())
    assert record == {
        "pysweph": VERSION,
        "module": MODULE,
        "sha256": sha256_hex(Path(swisseph.__file__)),
    }
    assert record["sha256"] == recorded_by_pysweph()


def test_a_replaced_module_gets_no_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    re_signed(tmp_path, monkeypatch)
    path = tmp_path / "binding-record.toml"
    with pytest.raises(BindingMismatch, match="is not the module pysweph"):
        write_build_record(path)
    assert not path.exists()


def test_the_command_writes_a_record(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "binding-record.toml"
    assert build_record.main([str(path)]) == 0
    assert capsys.readouterr().out == (
        f"wrote {path}: pysweph {VERSION}, {MODULE} as pysweph recorded it\n"
    )
    assert tomllib.loads(path.read_text())["sha256"] == recorded_by_pysweph()


def test_the_command_writes_nothing_for_a_replaced_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    re_signed(tmp_path, monkeypatch)
    path = tmp_path / "binding-record.toml"
    assert build_record.main([str(path)]) == 1
    error = capsys.readouterr().err
    assert error.startswith("no build record written: ")
    assert "is not the module pysweph" in error
    assert not path.exists()


# --- Using a record -------------------------------------------------------------------------


def test_with_its_record_a_re_signed_module_is_accepted(
    fetched_ephe: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "binding-record.toml"
    write_build_record(path)  # before signing
    re_signed(tmp_path, monkeypatch)
    with pytest.raises(BindingMismatch):
        verify_ephemeris(fetched_ephe)  # not yet in use
    use_build_record(path)
    assert verify_ephemeris(fetched_ephe).binding == f"pysweph {VERSION}"  # keys unchanged


def test_without_a_record_a_replaced_module_is_still_refused(
    fetched_ephe: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    re_signed(tmp_path, monkeypatch)
    with pytest.raises(BindingMismatch, match="is not the module pysweph"):
        verify_ephemeris(fetched_ephe)


def test_a_record_with_the_module_unchanged_changes_nothing(
    fetched_ephe: Path, tmp_path: Path
) -> None:
    path = tmp_path / "binding-record.toml"
    write_build_record(path)
    use_build_record(path)
    assert verify_ephemeris(fetched_ephe).binding == f"pysweph {VERSION}"


def valid(**fields: str) -> str:
    """A record's text: this module's, with `fields` replaced."""
    record = {"pysweph": VERSION, "module": MODULE, "sha256": recorded_by_pysweph()} | fields
    return "".join(f'{key} = "{value}"\n' for key, value in record.items())


@pytest.mark.parametrize(
    ("text", "why"),
    [
        (valid(pysweph="9.9.9"), f"is for pysweph 9.9.9; the project pins {VERSION}"),
        (valid(module="swisseph.other.so"), f"is for swisseph.other.so, not {MODULE}"),
        (valid(sha256="00" * 32), f"isn't the SHA-256 pysweph recorded for {MODULE}"),
        ('pysweph = "2.10', "can't be read"),
        (f'pysweph = "{VERSION}"\nmodule = "{MODULE}"\n', "can't be read: it has no sha256"),
        (valid() + "pysweph_is = 1\n", "can't be read: it has pysweph_is"),
        (valid().replace(f'"{MODULE}"', "3"), "can't be read: module isn't text"),
    ],
)
def test_a_record_that_doesn_t_fit_is_refused_and_relaxes_nothing(
    fetched_ephe: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    why: str,
) -> None:
    path = record_file(tmp_path, text)
    with pytest.raises(BindingMismatch) as refused:
        use_build_record(path)
    assert str(refused.value).startswith(f"build record {path} ")
    assert why in str(refused.value)
    re_signed(tmp_path, monkeypatch)
    with pytest.raises(BindingMismatch, match="is not the module pysweph"):
        verify_ephemeris(fetched_ephe)


def test_a_missing_record_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "nowhere.toml"
    with pytest.raises(BindingMismatch, match=re.escape(f"build record {path} is missing")):
        use_build_record(path)


def test_a_record_that_isn_t_utf_8_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "binding-record.toml"
    path.write_bytes(valid().encode("utf-16"))
    with pytest.raises(BindingMismatch, match=re.escape(f"build record {path} can't be read")):
        use_build_record(path)


def test_the_record_in_use_is_checked_again_at_each_calculation(
    fetched_ephe: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "binding-record.toml"
    write_build_record(path)
    use_build_record(path)
    other = tmp_path / "swisseph.other.so"  # a module of another name took its place
    other.write_bytes(b"not the pysweph build")
    monkeypatch.setattr(swisseph, "__file__", str(other))
    with pytest.raises(
        BindingMismatch, match=re.escape(f"the build record in use is for {MODULE}, not ")
    ):
        verify_ephemeris(fetched_ephe)


def test_the_installed_version_is_still_checked_with_a_record(
    fetched_ephe: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "binding-record.toml"
    write_build_record(path)
    use_build_record(path)
    monkeypatch.setattr(importlib.metadata, "requires", lambda name: ["pysweph==9.9.9"])
    with pytest.raises(BindingMismatch, match=re.escape("the project pins 9.9.9")):
        verify_ephemeris(fetched_ephe)
