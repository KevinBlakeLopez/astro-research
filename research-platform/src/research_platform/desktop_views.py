"""The desktop app's own views: the window's login, and the platform's static files.

The launcher arms a secret made at each launch, and the window opens the link holding it, which
logs in the local account once and opens the datasets page. A used, wrong or unarmed link is
not found; a wrong guess leaves the right link working. The local account is an admin with no
usable password, so nothing but the app's window can log in as it.

Django serves static files only in debug mode, which the app doesn't run in, so in the app this
serves them; elsewhere it leaves them to the server in front.
"""

import secrets
import threading

from django.conf import settings
from django.contrib.auth import login as log_in
from django.contrib.auth.models import User
from django.contrib.staticfiles.views import serve
from django.http import Http404, HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import redirect

from research_platform.desktop import LOCAL_USER

_lock = threading.Lock()
_armed: str | None = None


def arm(token: str) -> None:
    global _armed
    with _lock:
        _armed = token


def disarm() -> None:
    global _armed
    with _lock:
        _armed = None


def local_user() -> User:
    """The app's one account, made the first time it's needed."""
    user, made = User.objects.get_or_create(
        username=LOCAL_USER, defaults={"is_staff": True, "is_superuser": True}
    )
    if made:
        user.set_unusable_password()
        user.save()
    return user


def login(request: HttpRequest, token: str) -> HttpResponse:
    global _armed
    with _lock:
        if _armed is None or not secrets.compare_digest(token.encode(), _armed.encode()):
            raise Http404("no such login link")
        _armed = None  # it works once
    log_in(request, local_user(), backend="django.contrib.auth.backends.ModelBackend")
    return redirect("datasets")


def static(request: HttpRequest, path: str) -> HttpResponseBase:
    if not settings.RESEARCH_DESKTOP:
        raise Http404("static files are the server's to serve")
    return serve(request, path, insecure=True)
