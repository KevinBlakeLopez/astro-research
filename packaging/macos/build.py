"""Build the Mac app and its disk image, the same way on this Mac and on GitHub.

    uv run python packaging/macos/build.py

It needs an Apple silicon Mac with Xcode's command-line tools, and uv. In order, it:

1. builds this repository's two packages as wheels into `wheels/`, and proxy-tools' too, which
   PyPI has only as source (Briefcase installs only ready-built packages);
2. fetches the pinned ephemeris files into the app's own folder with
   `scripts/fetch_ephemeris.py`, which checks each file's SHA-256;
3. runs `briefcase create`, which installs the app's packages;
4. writes the build record with the app's own Python, while its swisseph module is still byte
   for byte as pysweph installed it (`python -m astro_research.build_record`);
5. runs `briefcase build`, which signs the app ad hoc and so changes that module's bytes;
6. runs `briefcase package`, which makes the disk image in `dist/`.

It starts afresh each time, deleting its own `build/`, `dist/` and `wheels/` first. Each
command is printed before it runs, and one that fails stops the script.
"""

import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
APP_FOLDER = HERE / "src" / "astro_research_app"
WHEELS = HERE / "wheels"
BRIEFCASE = ("uvx", "--python", "3.13", "briefcase@0.4.7")
PIP = ("uvx", "--python", "3.13", "pip@26.3")
CONTENTS = (
    HERE / "build" / "astro_research_app" / "macos" / "app" / "Astro Research.app" / "Contents"
)
PYTHON = CONTENTS / "Frameworks" / "Python.framework" / "Versions" / "3.13" / "bin" / "python3.13"
WRITE_RECORD = (
    "import sys; sys.path[:0] = [sys.argv[1]]; "
    "from astro_research.build_record import main; raise SystemExit(main([sys.argv[2]]))"
)


def run(*command: str, cwd: Path = REPO) -> None:
    print("+", " ".join(command), flush=True)
    finished = subprocess.run(command, cwd=cwd, check=False)
    if finished.returncode:
        raise SystemExit(f"stopped: {command[0]} exited with {finished.returncode}")


def pinned(name: str) -> str:
    """The app's requirement for `name`, as pyproject.toml pins it."""
    config = tomllib.loads((HERE / "pyproject.toml").read_text())
    requires: list[str] = config["tool"]["briefcase"]["app"]["astroresearch"]["requires"]
    (requirement,) = [r for r in requires if r.startswith(f"{name}==")]
    return requirement


def main() -> int:
    if sys.platform != "darwin":
        raise SystemExit("stopped: the Mac app is built on a Mac")
    for made in ("build", "dist", "wheels"):
        shutil.rmtree(HERE / made, ignore_errors=True)
    run("uv", "build", "--package", "astro-research", "--wheel", "--out-dir", str(WHEELS))
    run("uv", "build", "--package", "research-platform", "--wheel", "--out-dir", str(WHEELS))
    run(*PIP, "wheel", "--no-deps", "--wheel-dir", str(WHEELS), pinned("proxy-tools"))
    run("uv", "run", "python", "scripts/fetch_ephemeris.py", "--dir", str(APP_FOLDER / "ephe"))
    run(*BRIEFCASE, "create", "macOS", "app", "--no-input", cwd=HERE)
    record = CONTENTS / "Resources" / "app" / "astro_research_app" / "binding-record.toml"
    run(
        str(PYTHON),
        "-I",
        "-c",
        WRITE_RECORD,
        str(CONTENTS / "Resources" / "app_packages"),
        str(record),
    )
    run(*BRIEFCASE, "build", "macOS", "app", "--no-input", cwd=HERE)
    run(*BRIEFCASE, "package", "macOS", "app", "--adhoc-sign", "-p", "dmg", "--no-input", cwd=HERE)
    (image,) = (HERE / "dist").glob("*.dmg")
    print(f"built {image.relative_to(REPO)}: {image.stat().st_size / 2**20:.0f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
