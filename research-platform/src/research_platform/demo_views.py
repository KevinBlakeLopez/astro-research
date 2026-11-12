"""The desktop app's demo button: loading the Gauquelin demo (see `demo`).

It's offered on the home page in the app while there are no records. Elsewhere its address isn't
found. It looks `demo.A1` and `demo.fetch_https` up when pressed, so tests can replace them. A
refusal shows on the home page, which still offers the demo; loading opens the dataset's page,
saying what to do next.
"""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from research_platform import demo
from research_platform.nav_views import home_context


@login_required
@require_POST
def gauquelin(request: HttpRequest) -> HttpResponse:
    if not settings.RESEARCH_DESKTOP:
        raise Http404("the demo is the desktop app's")
    try:
        loaded = demo.load_gauquelin(demo.fetch_https, demo.A1)
    except demo.DemoRefused as error:
        context = home_context() | {"error": str(error)}
        return render(request, "research_platform/home.html", context, status=400)
    messages.success(
        request,
        f"Loaded the Gauquelin demo: {loaded.champions} champions and {loaded.controls} "
        "controls. Press Run to compute their charts. Then try a hypothesis: for example, a "
        "contact feature for Mars on the Ascendant or Midheaven, comparing the champions with "
        "the controls.",
    )
    return redirect("dataset", pk=loaded.dataset.pk)
