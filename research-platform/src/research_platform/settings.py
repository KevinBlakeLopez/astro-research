"""Django settings for the research platform.

It runs on this machine only: it answers on 127.0.0.1 and localhost, and every page needs a login.
Where its data is kept, its database and its session key come from the environment
(`configuration`): by default SQLite and the key in research-platform/, both kept out of git;
RESEARCH_DATABASE=postgresql chooses PostgreSQL. RESEARCH_DESKTOP=1 is set by the desktop app.
"""

import os
from pathlib import Path

import django_stubs_ext

from research_platform import configuration

# Lets classes written for the type checker, such as admin.ModelAdmin[Record], run.
django_stubs_ext.monkeypatch()

PLATFORM = Path(__file__).resolve().parents[2]
REPO = PLATFORM.parent


DATA_DIR = configuration.data_directory(os.environ, PLATFORM)
SECRET_KEY = configuration.secret_key(os.environ, DATA_DIR)
DEBUG = os.environ.get("RESEARCH_DEBUG") == "1"
RESEARCH_DESKTOP = os.environ.get("RESEARCH_DESKTOP") == "1"
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
                "research_platform.context_processors.desktop",
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

DATABASES = {"default": configuration.database(os.environ, DATA_DIR)}

# The pinned Swiss Ephemeris files (scripts/fetch_ephemeris.py).
EPHE_DIR = Path(os.environ.get("RESEARCH_EPHE_DIR", REPO / "ephe"))
