"""Django settings for the research platform.

The database defaults to the local PostgreSQL of research-platform/compose.yaml; set the
RESEARCH_DB_* variables to use another. There is no web interface yet, so there are no web
settings beyond what Django requires.
"""

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

SECRET_KEY = os.environ.get("RESEARCH_SECRET_KEY", "local-development-only-no-web-interface")
DEBUG = False
ALLOWED_HOSTS: list[str] = []
INSTALLED_APPS = ["research_platform"]
USE_TZ = True
TIME_ZONE = "UTC"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.environ.get("RESEARCH_DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("RESEARCH_DB_PORT", "55432"),
        "NAME": os.environ.get("RESEARCH_DB_NAME", "research"),
        "USER": os.environ.get("RESEARCH_DB_USER", "research"),
        "PASSWORD": os.environ.get("RESEARCH_DB_PASSWORD", "research-local-only"),
    }
}

# The pinned Swiss Ephemeris files (scripts/fetch_ephemeris.py).
EPHE_DIR = Path(os.environ.get("RESEARCH_EPHE_DIR", REPO / "ephe"))
