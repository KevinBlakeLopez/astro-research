"""What every page's template is told: whether it's shown in the desktop app."""

from django.conf import settings
from django.http import HttpRequest


def desktop(request: HttpRequest) -> dict[str, bool]:
    return {"desktop": settings.RESEARCH_DESKTOP}
