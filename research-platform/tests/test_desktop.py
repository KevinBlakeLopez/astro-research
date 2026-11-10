"""The platform as a desktop app: SQLite, one local account, and a server only this computer sees.

`python -m research_platform.desktop` opens the platform in its own window. It keeps its data
in the system's folder for app data (`app_data_directory`), applies database migrations at every
launch, and makes one local account with no password. The window logs in with a secret made at
each launch, which works once and opens the home page; outside the app no such link works. The
server answers only on 127.0.0.1, at a free port, and serves the platform's own static files
(HTMX) itself.

The database is SQLite unless RESEARCH_DATABASE=postgresql (`configuration.database`), and the
data folder is RESEARCH_DATA_DIR or else the platform's own folder, where its session key has
always been (`configuration.data_directory`).

The window itself isn't tested here: that's for the steps in docs/RESEARCH_PLATFORM.md.
"""

import hashlib
import http.client
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import Settings

from astro_research.chart import BirthRecord, ChartSettings
from astro_research.civil_time import ZonedLocalTime
from astro_research.ephemeris import Ephemeris
from astro_research.geo import GeoPosition
from astro_research.measurements import MAJOR
from research_platform import configuration, desktop, desktop_views
from research_platform.models import Dataset, Record
from research_platform.runner import run_dataset

MIGRATIONS = Path(__file__).parents[1] / "src/research_platform/migrations"
HTMX = "/static/research_platform/htmx-2.0.11.min.js"
HTMX_SHA256 = "d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717"  # test_web.py
FIRST = BirthRecord(
    time=ZonedLocalTime(local=datetime(1987, 6, 15, 4, 25), zone="Europe/London"),
    place=GeoPosition(latitude=51.5074, longitude=-0.1278),
)


@pytest.fixture
def armed() -> Iterator[str]:
    """The app's launch secret, as the launcher sets it."""
    token = "a-synthetic-launch-secret"
    desktop_views.arm(token)
    yield token
    desktop_views.disarm()


@pytest.fixture
def server() -> Iterator[desktop.Server]:
    running = desktop.serve()
    yield running
    running.stop()


def get(server: desktop.Server, path: str, cookie: str = "") -> http.client.HTTPResponse:
    connection = http.client.HTTPConnection(server.host, server.port, timeout=30)
    connection.request("GET", path, headers={"Cookie": cookie} if cookie else {})
    return connection.getresponse()


def session_cookie(response: http.client.HTTPResponse) -> str:
    for header in response.headers.get_all("Set-Cookie") or []:
        if header.startswith("sessionid="):
            return header.split(";", 1)[0]
    raise AssertionError("no session cookie")


# --- Settings -------------------------------------------------------------------------------


def test_the_database_is_sqlite_in_the_data_folder_by_default(tmp_path: Path) -> None:
    for environ in ({}, {"RESEARCH_DATABASE": "sqlite"}):
        assert configuration.database(environ, tmp_path) == {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(tmp_path / "research.sqlite3"),
        }


def test_postgresql_is_still_there_when_asked_for(tmp_path: Path) -> None:
    assert configuration.database({"RESEARCH_DATABASE": "postgresql"}, tmp_path) == {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": "127.0.0.1",
        "PORT": "55432",
        "NAME": "research",
        "USER": "research",
        "PASSWORD": "research-local-only",
    }
    environ = {
        "RESEARCH_DATABASE": "postgresql",
        "RESEARCH_DB_HOST": "db.test",
        "RESEARCH_DB_PORT": "5432",
        "RESEARCH_DB_NAME": "other",
        "RESEARCH_DB_USER": "someone",
        "RESEARCH_DB_PASSWORD": "synthetic",
    }
    chosen = configuration.database(environ, tmp_path)
    assert (chosen["HOST"], chosen["PORT"], chosen["NAME"], chosen["USER"]) == (
        "db.test",
        "5432",
        "other",
        "someone",
    )
    assert chosen["PASSWORD"] == "synthetic"


def test_an_unknown_database_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ImproperlyConfigured, match="'mysql' is not one of postgresql, sqlite"):
        configuration.database({"RESEARCH_DATABASE": "mysql"}, tmp_path)


def test_the_data_folder_is_research_data_dir_or_the_platform_s_own(tmp_path: Path) -> None:
    platform = tmp_path / "research-platform"
    assert configuration.data_directory({}, platform) == platform
    assert configuration.data_directory({"RESEARCH_DATA_DIR": "/data/here"}, platform) == Path(
        "/data/here"
    )


def test_the_session_key_is_made_once_in_the_data_folder(tmp_path: Path) -> None:
    key = configuration.secret_key({}, tmp_path)
    path = tmp_path / ".secret_key"
    assert path.read_text().strip() == key and len(key) >= 50
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
    assert configuration.secret_key({}, tmp_path) == key  # kept, not made again
    assert configuration.secret_key({"RESEARCH_SECRET_KEY": "given"}, tmp_path) == "given"


@pytest.mark.parametrize(
    ("system", "environ", "folder"),
    [
        ("Darwin", {}, "/Users/someone/Library/Application Support"),
        (
            "Windows",
            {"APPDATA": "/Profiles/someone/Roaming"},
            "/Profiles/someone/Roaming",
        ),
        ("Windows", {}, "/Users/someone/AppData/Roaming"),
        ("Linux", {"XDG_DATA_HOME": "/Users/someone/data"}, "/Users/someone/data"),
        ("Linux", {}, "/Users/someone/.local/share"),
    ],
)
def test_the_app_keeps_its_data_in_the_system_s_folder_for_app_data(
    system: str, environ: dict[str, str], folder: str
) -> None:
    home = Path("/Users/someone")
    found = desktop.app_data_directory(system=system, home=home, environ=environ)
    assert found == Path(folder) / desktop.APP_NAME


def test_outside_the_app_the_desktop_setting_is_off() -> None:
    assert "RESEARCH_DESKTOP" not in os.environ  # only the launcher sets it
    assert settings.RESEARCH_DESKTOP is False


# --- Preparing the data folder at launch ----------------------------------------------------


def prepare(folder: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    environ = extra | {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("RESEARCH_DATA")
        and name
        not in {
            "RESEARCH_DATABASE",
            "RESEARCH_SECRET_KEY",
            "DJANGO_SETTINGS_MODULE",
        }
    }
    command = [sys.executable, "-m", "research_platform.desktop", "--prepare", str(folder)]
    return subprocess.run(command, env=environ, capture_output=True, text=True, timeout=300)


def test_a_launch_prepares_the_data_folder_and_a_second_changes_nothing(tmp_path: Path) -> None:
    folder = tmp_path / "data"
    keys = []
    # The second launch has PostgreSQL chosen, and unreachable: the app uses its SQLite anyway.
    postgresql = {"RESEARCH_DATABASE": "postgresql", "RESEARCH_DB_HOST": "unused.invalid"}
    for launch, chosen in ((1, {}), (2, postgresql)):
        done = prepare(folder, **chosen)
        assert done.returncode == 0, done.stderr
        with sqlite3.connect(folder / "research.sqlite3") as db:
            applied = {
                name
                for (name,) in db.execute(
                    "SELECT name FROM django_migrations WHERE app = 'research_platform'"
                )
            }
            users = db.execute("SELECT username, is_superuser, password FROM auth_user").fetchall()
        assert applied == {path.stem for path in MIGRATIONS.glob("0*.py")}, launch
        assert len(users) == 1, launch
        username, is_superuser, password = users[0]
        assert (username, is_superuser) == (desktop.LOCAL_USER, 1)
        assert password.startswith("!"), "the local account has no usable password"
        keys.append((folder / ".secret_key").read_text())
    assert keys[0] == keys[1], "the session key is kept between launches"
    if os.name == "posix":
        assert folder.stat().st_mode & 0o777 == 0o700
        assert (folder / ".secret_key").stat().st_mode & 0o777 == 0o600


# --- The window's login ---------------------------------------------------------------------


@pytest.mark.django_db
def test_the_launch_link_logs_the_window_in_once(client: Client, armed: str) -> None:
    link = reverse("desktop-login", args=[armed])
    response = client.get(link)
    assert response.status_code == 302 and response["Location"] == reverse("home")
    shown = client.get(reverse("datasets"))
    assert shown.status_code == 200
    user = shown.wsgi_request.user
    assert user.get_username() == desktop.LOCAL_USER and user.is_superuser
    assert not user.has_usable_password()
    assert Client().get(link).status_code == 404  # used


@pytest.mark.django_db
def test_a_wrong_link_is_refused_and_the_right_one_still_works(client: Client, armed: str) -> None:
    assert client.get(reverse("desktop-login", args=["a-guess"])).status_code == 404
    assert client.get(reverse("datasets")).status_code == 302  # still not logged in
    assert client.get(reverse("desktop-login", args=[armed])).status_code == 302


@pytest.mark.django_db
def test_no_launch_link_works_outside_the_app(client: Client) -> None:
    desktop_views.disarm()
    assert client.get(reverse("desktop-login", args=["anything"])).status_code == 404
    assert not get_user_model().objects.exists()


@pytest.mark.django_db
def test_the_local_account_is_made_once(client: Client, armed: str) -> None:
    client.get(reverse("desktop-login", args=[armed]))
    desktop_views.arm("another-launch")
    Client().get(reverse("desktop-login", args=["another-launch"]))
    assert get_user_model().objects.filter(username=desktop.LOCAL_USER).count() == 1


@pytest.mark.django_db
def test_in_the_app_the_header_has_no_log_out(admin_client: Client, settings: Settings) -> None:
    logout = f'action="{reverse("logout")}"'
    settings.RESEARCH_DESKTOP = True
    assert logout not in admin_client.get(reverse("datasets")).content.decode()
    settings.RESEARCH_DESKTOP = False
    assert logout in admin_client.get(reverse("datasets")).content.decode()


# --- Static files ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_app_serves_htmx_itself(client: Client, settings: Settings) -> None:
    settings.RESEARCH_DESKTOP = True
    response: Any = client.get(HTMX)  # a file, streamed
    assert response.status_code == 200
    content = b"".join(response.streaming_content)
    assert hashlib.sha256(content).hexdigest() == HTMX_SHA256


@pytest.mark.django_db
def test_outside_the_app_static_files_are_left_to_the_server(
    client: Client, settings: Settings
) -> None:
    settings.RESEARCH_DESKTOP = False
    settings.DEBUG = False
    assert client.get(HTMX).status_code == 404


# --- The app's server -----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_the_server_answers_only_on_this_computer_and_needs_a_login(
    server: desktop.Server,
) -> None:
    assert server.host == "127.0.0.1" and server.port > 0
    response = get(server, reverse("datasets"))
    assert response.status == 302 and "/login/" in (response.getheader("Location") or "")


@pytest.mark.django_db(transaction=True)
def test_the_window_logs_in_through_the_server(server: desktop.Server, armed: str) -> None:
    response = get(server, reverse("desktop-login", args=[armed]))
    assert response.status == 302
    cookie = session_cookie(response)
    shown = get(server, reverse("datasets"), cookie)
    assert shown.status == 200 and b"Datasets" in shown.read()


@pytest.mark.django_db(transaction=True)
def test_the_server_serves_htmx(server: desktop.Server, settings: Settings) -> None:
    settings.RESEARCH_DESKTOP = True
    response = get(server, HTMX)
    assert response.status == 200
    assert hashlib.sha256(response.read()).hexdigest() == HTMX_SHA256


@pytest.mark.django_db(transaction=True)
def test_downloads_come_through_the_server_unchanged(
    server: desktop.Server, armed: str, store: Callable[..., Record], eph: Ephemeris
) -> None:
    dataset = Dataset.objects.create(name="sample")
    dataset.records.add(store(FIRST))
    run = run_dataset(dataset, ChartSettings(aspect_angles=MAJOR), eph)
    cookie = session_cookie(get(server, reverse("desktop-login", args=[armed])))
    direct = Client()
    direct.force_login(get_user_model().objects.get(username=desktop.LOCAL_USER))
    for name in ("run-outcomes-csv", "run-charts-jsonl", "run-pairs-csv"):
        address = reverse(name, args=[run.pk])
        through = get(server, address, cookie)
        assert through.status == 200, name
        assert (through.getheader("Content-Disposition") or "").startswith("attachment;"), name
        expected: Any = direct.get(address)  # streamed or not
        body = b"".join(expected.streaming_content) if expected.streaming else expected.content
        assert through.read() == body, name


@pytest.mark.django_db(transaction=True)
def test_a_stopped_server_answers_no_more(server: desktop.Server) -> None:
    server.stop()
    with pytest.raises(ConnectionRefusedError):  # nothing listening, not merely slow
        get(server, reverse("datasets"))
    server.stop()  # stopping twice is harmless
