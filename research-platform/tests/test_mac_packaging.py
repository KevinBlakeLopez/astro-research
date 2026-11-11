"""The Mac app's pinned dependencies, kept equal to uv.lock's.

`packaging/macos/pyproject.toml` lists the app's requirements for Briefcase, which installs
them with pip rather than from uv.lock. The list must be exactly what `uv export` gives for the
platform's `desktop` extra on an Apple silicon Mac, plus this repository's two packages at their
own versions and `std-nslog`, which only the app needs (it sends the app's output to the macOS
log). So a version changed in one can't silently miss the other.
"""

import re
import subprocess
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

REPO = Path(__file__).resolve().parents[2]
SETUP = REPO / "packaging" / "macos" / "pyproject.toml"
APPLE_SILICON_MAC = {
    "os_name": "posix",
    "sys_platform": "darwin",
    "platform_system": "Darwin",
    "platform_machine": "arm64",
    "implementation_name": "cpython",
    "platform_python_implementation": "CPython",
    "python_version": "3.13",
    "python_full_version": "3.13.11",
}
APP_ONLY = {"std-nslog==2.0.0"}


def version(project: Path) -> str:
    value: str = tomllib.loads((project / "pyproject.toml").read_text())["project"]["version"]
    return value


def locked_for_the_app() -> set[str]:
    """uv.lock's pins for the desktop extra, as `name==version`, on an Apple silicon Mac."""
    exported = subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--package",
            "research-platform",
            "--extra",
            "desktop",
            "--no-dev",
            "--no-hashes",
            "--no-emit-workspace",
            "--no-header",
            "--no-annotate",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    pins = set()
    for line in exported.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker is None or requirement.marker.evaluate(APPLE_SILICON_MAC):
            (pinned,) = requirement.specifier
            assert pinned.operator == "==", line
            pins.add(f"{requirement.name}=={pinned.version}")
    return pins


def requires() -> list[str]:
    config = tomllib.loads(SETUP.read_text())
    listed: list[str] = config["tool"]["briefcase"]["app"]["astroresearch"]["requires"]
    return listed


def test_the_app_s_pins_are_uv_lock_s() -> None:
    ours = {
        f"astro-research=={version(REPO)}",
        f"research-platform=={version(REPO / 'research-platform')}",
    }
    assert set(requires()) == locked_for_the_app() | ours | APP_ONLY


def test_each_requirement_is_one_exact_pin_listed_once() -> None:
    listed = requires()
    assert len(listed) == len(set(listed))
    for requirement in listed:
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]*==[0-9][\w.]*", requirement), requirement


def test_the_lock_s_pins_include_what_the_app_can_t_do_without() -> None:
    names = {pin.split("==")[0] for pin in locked_for_the_app()}
    assert {"django", "pysweph", "pywebview", "waitress", "tzdata", "pyobjc-core"} <= names
    assert "pythonnet" not in names  # Windows only
