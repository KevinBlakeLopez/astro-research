"""The Mac app's launcher: what the app Briefcase builds runs (see `packaging/macos/`).

The app carries its own ephemeris files and the build record its build wrote, in its own
folder. `main(folder)` points `RESEARCH_EPHE_DIR` at the files, whatever it was set to; hands
the record to `use_build_record` before anything is calculated, since signing the app changed
the `swisseph` module's bytes after the build checked them; and then opens the window as
`desktop.main([])` does, whatever arguments the app was started with. A refused record stops
it with the engine's reason, before any window opens.
"""

import os
from pathlib import Path

from astro_research import ephemeris
from research_platform import desktop

EPHEMERIS = "ephe"
RECORD = "binding-record.toml"


def main(folder: Path) -> int:
    """Open the window of the app whose own folder is `folder`; its exit code."""
    os.environ["RESEARCH_EPHE_DIR"] = str(folder / EPHEMERIS)
    ephemeris.use_build_record(folder / RECORD)
    return desktop.main([])
