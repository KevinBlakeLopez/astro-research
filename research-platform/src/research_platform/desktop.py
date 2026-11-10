"""The research platform as a desktop app, in its own window.

    uv run --package research-platform --extra desktop python -m research_platform.desktop

At each launch it:

1. keeps its data in the system's folder for app data (`app_data_directory`), readable only by
   you: the SQLite database and the session key;
2. applies the database's migrations, so a new version upgrades the data as it opens, and makes
   the one local account if it isn't there yet (`prepare`);
3. serves the platform with waitress on 127.0.0.1, at a free port, so only this computer can
   reach it (`serve`);
4. opens a pywebview window at a link holding a secret made for this launch, which logs the
   window in once (`desktop_views`), and stops the server when the window closes.

`--prepare FOLDER` does steps 1 and 2 in FOLDER and exits, without a window.
"""

import argparse
import os
import platform
import secrets
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

APP_NAME = "Astro Research"  # a placeholder until the app is named
LOCAL_USER = "local"
HOST = "127.0.0.1"


def app_data_directory(*, system: str, home: Path, environ: Mapping[str, str]) -> Path:
    if system == "Darwin":
        base = home / "Library" / "Application Support"
    elif system == "Windows":
        base = Path(environ["APPDATA"]) if environ.get("APPDATA") else home / "AppData" / "Roaming"
    elif environ.get("XDG_DATA_HOME"):
        base = Path(environ["XDG_DATA_HOME"])
    else:
        base = home / ".local" / "share"
    return base / APP_NAME


def prepare(folder: Path) -> None:
    """Sets this process up as the app, keeping its data in `folder`, and readies the data."""
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        folder.chmod(0o700)
    os.environ.update(
        {
            "RESEARCH_DATA_DIR": str(folder),
            "RESEARCH_DATABASE": "sqlite",
            "RESEARCH_DESKTOP": "1",
            "DJANGO_SETTINGS_MODULE": "research_platform.settings",
        }
    )
    import django

    django.setup()
    from django.core.management import call_command

    from research_platform.desktop_views import local_user

    call_command("migrate", interactive=False, verbosity=0)
    local_user()


@dataclass
class Server:
    """The app's waitress server, running in a thread of its own."""

    host: str
    port: int
    _wsgi: Any = field(repr=False)
    _thread: threading.Thread = field(repr=False)
    _stopped: bool = False

    def stop(self) -> None:
        """Closes the server and every open connection; stopping twice does nothing."""
        if self._stopped:
            return
        self._stopped = True
        from waitress import wasyncore

        wsgi = self._wsgi
        # waitress's loop isn't thread-safe, so the closing is handed to the loop's own thread;
        # with nothing left open, the loop ends.
        wsgi.trigger.pull_trigger(lambda: wasyncore.close_all(wsgi._map))
        self._thread.join(timeout=10)
        wsgi.task_dispatcher.shutdown()


def serve() -> Server:
    from django.core.wsgi import get_wsgi_application
    from waitress.server import BaseWSGIServer, create_server

    wsgi = create_server(get_wsgi_application(), host=HOST, port=0, threads=4)
    # One address gives one server, listening on its own socket.
    assert isinstance(wsgi, BaseWSGIServer) and wsgi.socket is not None
    thread = threading.Thread(target=wsgi.run, name="research-platform-server", daemon=True)
    thread.start()
    port = int(wsgi.socket.getsockname()[1])  # the free port it was given
    return Server(host=HOST, port=port, _wsgi=wsgi, _thread=thread)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m research_platform.desktop",
        description="Opens the research platform in its own window.",
    )
    parser.add_argument(
        "--prepare",
        type=Path,
        metavar="FOLDER",
        help="set FOLDER up as the app's data folder and exit, without opening a window",
    )
    args = parser.parse_args(argv)
    if args.prepare is not None:
        prepare(args.prepare)
        return 0
    prepare(app_data_directory(system=platform.system(), home=Path.home(), environ=os.environ))

    import webview
    from django.urls import reverse

    from research_platform import desktop_views

    token = secrets.token_urlsafe(32)
    desktop_views.arm(token)
    server = serve()
    webview.settings["ALLOW_DOWNLOADS"] = True
    address = f"http://{server.host}:{server.port}{reverse('desktop-login', args=[token])}"
    webview.create_window(APP_NAME, address, width=1280, height=860, min_size=(800, 600))
    try:
        webview.start()  # until the window closes
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
