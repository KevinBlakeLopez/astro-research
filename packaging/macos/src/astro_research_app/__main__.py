"""Start the app: the launcher, with this folder's ephemeris files and build record."""

import sys
from pathlib import Path

from research_platform.mac_app import main

sys.exit(main(Path(__file__).parent))
