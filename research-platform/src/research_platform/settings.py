"""Django settings for the research platform.

It runs on this machine only: it answers on 127.0.0.1 and localhost, and every page needs a login.
The database defaults to the local PostgreSQL of research-platform/compose.yaml; set the
RESEARCH_DB_* variables to use another.

The secret key signs sessions. It comes from RESEARCH_SECRET_KEY, or else from
research-platform/.secret_key, which is made on first use and kept out of git.
"""

import os
import secrets
from pathlib import Path

import django_stubs_ext

# Lets classes written for the type checker, such as admin.ModelAdmin[Record], run.
django_stubs_ext.monkeypatch()

PLATFORM = Path(__file__).resolve().parents[2]
REPO = PLATFORM.parent


def _secret_key() -> str:
    if key := os.environ.get("RESEARCH_SECRET_KEY"):
        return key
    path = PLATFORM / ".secret_key"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(50))
        path.chmod(0o600)
    return path.read_text().strip()


SECRET_KEY = _secret_key()
DEBUG = os.environ.get("RESEARCH_DEBUG") == "1"
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "research_platform",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "research_platform.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]
STATIC_URL = "static/"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "admin:index"
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
