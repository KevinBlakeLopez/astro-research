"""The Mac app's launcher: what the app Briefcase builds runs to open the window.

`research_platform.mac_app.main(folder)` is called with the app's own folder, which holds the
ephemeris files (`ephe/`) and the build record (`binding-record.toml`) that its build wrote.
It:

- points `RESEARCH_EPHE_DIR` at those files, whatever it was set to before;
- hands the record to `use_build_record`, before anything is calculated;
- then opens the window as `desktop.main([])` does (no arguments, whatever the app was started
  with), and returns its exit code.

A record that's refused stops it with the engine's reason, before any window opens.
"""

import os
import re
from collections.abc import Sequence
from pathlib import Path

import pytest

from astro_research import ephemeris
from astro_research.ephemeris import BindingMismatch, write_build_record
from research_platform import desktop, mac_app


@pytest.fixture(autouse=True)
def no_build_record_in_use(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ephemeris, "_build_record", None)


@pytest.fixture
def app_folder(tmp_path: Path) -> Path:
    """An app's folder as its build leaves it: ephemeris files and a build record."""
    folder = tmp_path / "astro_research_app"
    (folder / "ephe").mkdir(parents=True)
    write_build_record(folder / "binding-record.toml")
    return folder


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """What the launcher did, in order. The window is stood in for, so none opens."""
    happened: list[str] = []
    use = ephemeris.use_build_record

    def using(path: Path) -> None:
        happened.append(f"use {path}")
        use(path)

    def window(argv: Sequence[str] | None = None) -> int:
        happened.append(f"window {argv!r} with {os.environ['RESEARCH_EPHE_DIR']}")
        return 0

    monkeypatch.setattr(ephemeris, "use_build_record", using)
    monkeypatch.setattr(desktop, "main", window)
    monkeypatch.setenv("RESEARCH_EPHE_DIR", "/somewhere/else")  # and put back afterwards
    return happened


def test_the_window_opens_with_the_app_s_ephemeris_and_its_record_in_use(
    app_folder: Path, events: list[str]
) -> None:
    assert mac_app.main(app_folder) == 0
    assert events == [
        f"use {app_folder / 'binding-record.toml'}",
        f"window [] with {app_folder / 'ephe'}",
    ]
    assert ephemeris._build_record is not None


def test_the_window_s_exit_code_is_the_launcher_s(
    app_folder: Path, events: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop, "main", lambda argv=None: 3)
    assert mac_app.main(app_folder) == 3


def test_a_refused_record_opens_no_window(app_folder: Path, events: list[str]) -> None:
    record = app_folder / "binding-record.toml"
    record.write_text(record.read_text().replace('pysweph = "', 'pysweph = "9.9.9-'))
    with pytest.raises(BindingMismatch, match=re.escape("is for pysweph 9.9.9-")):
        mac_app.main(app_folder)
    assert events == [f"use {record}"]
    assert ephemeris._build_record is None


def test_a_missing_record_opens_no_window(app_folder: Path, events: list[str]) -> None:
    record = app_folder / "binding-record.toml"
    record.unlink()
    with pytest.raises(BindingMismatch, match="is missing"):
        mac_app.main(app_folder)
    assert events == [f"use {record}"]
