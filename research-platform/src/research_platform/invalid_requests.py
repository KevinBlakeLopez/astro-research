"""Refused requests, shown where they happened, with debug mode on or off.

Settings, offsets or a filter a page can't use raise `InvalidRequest`, whose message says why.
Django's own BadRequest isn't used: with debug mode off Django shows a bare "Bad Request (400)",
and with it on a traceback page (both observed). Instead, with status 400:

- a page renders its own template with its forms as submitted and the reason above them (each
  page view does this itself, since only it knows its forms);
- a fragment is just the reason (`fragment`), which the base page tells HTMX to swap in;
- a download is the reason in plain text (`download`).
"""

import functools
from collections.abc import Callable
from typing import Concatenate

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


class InvalidRequest(ValueError):
    """Settings, offsets or a filter the page can't use; the message says why."""


def fragment[**P](
    view: Callable[Concatenate[HttpRequest, P], HttpResponse],
) -> Callable[Concatenate[HttpRequest, P], HttpResponse]:
    """A fragment view whose refusal is the reason alone, for HTMX to swap in."""

    @functools.wraps(view)
    def shown(request: HttpRequest, /, *args: P.args, **kwargs: P.kwargs) -> HttpResponse:
        try:
            return view(request, *args, **kwargs)
        except InvalidRequest as error:
            context = {"error": str(error)}
            return render(request, "research_platform/request_error.html", context, status=400)

    return shown


def download[**P](
    view: Callable[Concatenate[HttpRequest, P], HttpResponse],
) -> Callable[Concatenate[HttpRequest, P], HttpResponse]:
    """A download view whose refusal is the reason in plain text."""

    @functools.wraps(view)
    def shown(request: HttpRequest, /, *args: P.args, **kwargs: P.kwargs) -> HttpResponse:
        try:
            return view(request, *args, **kwargs)
        except InvalidRequest as error:
            return HttpResponse(f"{error}\n", status=400, content_type="text/plain")

    return shown
