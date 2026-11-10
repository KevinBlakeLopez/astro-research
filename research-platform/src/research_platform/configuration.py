"""Where the platform keeps its data, which database it uses, and its session key.

Each reads the environment it's given, so the desktop app and the tests choose without editing
the settings:

- the data folder is RESEARCH_DATA_DIR, or else the platform's own folder, where its session key
  has always been kept;
- the database is SQLite, in the data folder, unless RESEARCH_DATABASE=postgresql chooses the
  PostgreSQL of research-platform/compose.yaml (or another, with the RESEARCH_DB_* variables);
- the session key is RESEARCH_SECRET_KEY, or else the data folder's .secret_key, made on first
  use and readable only by you.
"""

import os
import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from django.core.exceptions import ImproperlyConfigured

DATABASES = ("postgresql", "sqlite")
SQLITE_FILE = "research.sqlite3"


def data_directory(environ: Mapping[str, str], platform: Path) -> Path:
    if folder := environ.get("RESEARCH_DATA_DIR"):
        return Path(folder)
    return platform


def database(environ: Mapping[str, str], data_dir: Path) -> dict[str, Any]:
    chosen = environ.get("RESEARCH_DATABASE", "sqlite")
    if chosen == "sqlite":
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": str(data_dir / SQLITE_FILE)}
    if chosen == "postgresql":
        return {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": environ.get("RESEARCH_DB_HOST", "127.0.0.1"),
            "PORT": environ.get("RESEARCH_DB_PORT", "55432"),
            "NAME": environ.get("RESEARCH_DB_NAME", "research"),
            "USER": environ.get("RESEARCH_DB_USER", "research"),
            "PASSWORD": environ.get("RESEARCH_DB_PASSWORD", "research-local-only"),
        }
    raise ImproperlyConfigured(f"RESEARCH_DATABASE {chosen!r} is not one of {', '.join(DATABASES)}")


def secret_key(environ: Mapping[str, str], data_dir: Path) -> str:
    if key := environ.get("RESEARCH_SECRET_KEY"):
        return key
    path = data_dir / ".secret_key"
    if not path.exists():
        # Made readable only by you from the start, not chmodded after it's written.
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(handle, "w") as file:
            file.write(secrets.token_urlsafe(50))
    return path.read_text().strip()
