from django import template
from django.utils import timezone

register = template.Library()

COLORS = {
    "Arsenal": "#EF0107", "Aston Villa": "#7B003C", "Bournemouth": "#DA291C", "Brentford": "#E30613",
    "Brighton": "#0057B8", "Chelsea": "#034694", "Coventry": "#59B7E1", "Crystal Palace": "#1B458F",
    "Everton": "#003399", "Fulham": "#1c1c1e", "Hull": "#F5A12D", "Ipswich": "#3A64A3", "Leeds": "#E6B800",
    "Liverpool": "#C8102E", "Man City": "#6CABDD", "Man United": "#DA291C", "Newcastle": "#3a3a3c",
    "Nott'm Forest": "#DD0000", "Sunderland": "#EB172B", "Tottenham": "#132257", "Burnley": "#6C1D45",
    "Wolves": "#FDB913", "West Ham": "#7A263A", "Leicester": "#003090", "Southampton": "#D71920",
}


@register.filter
def pct(v, digits=0):
    try:
        return f"{float(v) * 100:.{int(digits)}f}%"
    except (TypeError, ValueError):
        return "–"


@register.filter
def pct_n(v):
    try:
        return round(float(v) * 100)
    except (TypeError, ValueError):
        return 0


@register.filter
def team_color(name):
    return COLORS.get(getattr(name, "name", name), "#8e8e93")


@register.filter
def dec(v, n=1):
    try:
        return f"{float(v):.{int(n)}f}"
    except (TypeError, ValueError):
        return "–"


@register.filter
def get(d, k):
    try:
        return d.get(k)
    except AttributeError:
        return None


@register.filter
def local_iso(dt):
    return timezone.localtime(dt).isoformat()


@register.filter
def pick_word(letter):
    return {"H": "Home win", "D": "Draw", "A": "Away win"}.get(letter, "")


@register.filter
def times100(v):
    try:
        return float(v) * 100
    except (TypeError, ValueError):
        return 0


@register.filter
def heat(v):
    """0..1 -> opacity for heat cells (sqrt so small probabilities stay visible)."""
    try:
        return round(min(1.0, float(v) ** 0.5 * 1.15), 3)
    except (TypeError, ValueError):
        return 0
