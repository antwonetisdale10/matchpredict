from django.conf import settings


def site(request):
    can_studio = request.user.is_authenticated and (request.user.is_staff or not settings.DATA_STUDIO_STAFF_ONLY)
    return {"season": getattr(request, "season", None), "predicted_seasons": getattr(request, "predicted_seasons", []),
            "can_studio": can_studio}
