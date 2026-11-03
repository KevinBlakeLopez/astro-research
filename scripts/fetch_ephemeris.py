"""Download the pinned Swiss Ephemeris data files into ephe/ and verify the environment.

    uv run python scripts/fetch_ephemeris.py [--dir PATH]

Files already present with the right SHA-256 are kept. A present file that doesn't match is
reported and left alone; delete it to have it fetched again. Each download is checked against
the manifest's size and SHA-256 before it is moved into place.
"""

import argparse
import hashlib
import sys
import tempfile
import urllib.request
from pathlib import Path

from astro_research.ephemeris import EphemerisEnvironmentError, load_manifest, verify_ephemeris

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "ephe"


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch and verify the pinned ephemeris files.")
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="target folder")
    target: Path = parser.parse_args().dir
    target.mkdir(exist_ok=True)
    manifest = load_manifest()

    for entry in manifest.files:
        destination = target / entry.name
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() == entry.sha256:
                print(f"present   {entry.name}")
                continue
            print(f"MISMATCH  {destination} differs from the manifest; delete it", file=sys.stderr)
            return 1
        url = manifest.url(entry.name)
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
        digest = hashlib.sha256(data).hexdigest()
        if len(data) != entry.size or digest != entry.sha256:
            print(f"REJECTED  {url}: {len(data)} bytes, sha256 {digest}", file=sys.stderr)
            return 1
        # Staged as a dotfile, which verification ignores, then moved into place in one step.
        with tempfile.NamedTemporaryFile(dir=target, prefix=".", delete=False) as staged:
            staged.write(data)
        Path(staged.name).replace(destination)
        print(f"fetched   {entry.name} ({len(data)} bytes)")

    try:
        provenance = verify_ephemeris(target)
    except EphemerisEnvironmentError as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1
    print(
        f"verified  DE{provenance.de_number} via {provenance.binding} "
        f"(Swiss Ephemeris {provenance.swisseph_version})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
