from django.conf import settings

from .models import Season


class SeasonMiddleware:
    """Attach request.season: ?season=2026 (remembered in the session) else the target season."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        seasons = list(Season.objects.filter(matches__prediction__isnull=False).distinct().order_by("start_year"))
        pick = request.GET.get("season")
        if pick and pick.isdigit():
            request.session["season"] = int(pick)
        want = request.session.get("season", settings.TARGET_SEASON)
        request.season = next((s for s in seasons if s.start_year == want), None) or (seasons[-1] if seasons else None)
        request.predicted_seasons = seasons
        return self.get_response(request)
