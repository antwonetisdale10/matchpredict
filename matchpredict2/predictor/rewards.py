"""Scoring, levels, badges and streaks for user predictions.

Everything here runs when real results are recorded for a match (see settle_match). It never
touches the prediction model - results only decide who earns points.
"""
from django.db.models import Sum

from .models import Match, MatchEvent, UserBadge, UserPrediction

POINTS = {
    "result": 3, "exact": 5, "ht": 2, "first_window": 2, "first_scorer": 4,
    "red_yes": 4, "red_no": 1, "yellows": 1, "corners": 1, "upset": 3,
}
LEVELS = [(0, "Rookie"), (25, "Fan"), (75, "Analyst"), (175, "Pundit"), (350, "Mastermind"), (700, "Oracle")]
BADGES = {
    "first_blood": ("First blood", "Called your first result correctly"),
    "sniper": ("Sniper", "Nailed an exact score"),
    "hat_trick": ("Hat-trick", "3 correct results in a row"),
    "perfect_week": ("Perfect week", "Every result right in a matchweek (5+ picks)"),
    "upset_caller": ("Upset caller", "Backed an outcome the model gave under 25%, and it came in"),
    "card_shark": ("Card shark", "Called a red card"),
    "oracle": ("Oracle", "5 exact scores"),
    "regular": ("Matchday regular", "Made 20 predictions"),
    "beat_model": ("Model beater", "Out-scored the model over 10+ settled picks"),
}


def level_for(points):
    cur, nxt = LEVELS[0], None
    for i, (need, name) in enumerate(LEVELS):
        if points >= need:
            cur = (need, name)
            nxt = LEVELS[i + 1] if i + 1 < len(LEVELS) else None
    return {"name": cur[1], "floor": cur[0], "next_name": nxt[1] if nxt else None, "next_at": nxt[0] if nxt else None,
            "progress": 1.0 if not nxt else (points - cur[0]) / (nxt[0] - cur[0])}


def result_letter(h, a):
    return "H" if h > a else "A" if h < a else "D"


def window_of(minute):
    for lo, hi, key in ((1, 15, "1-15"), (16, 30, "16-30"), (31, 45, "31-45"), (46, 60, "46-60"), (61, 75, "61-75")):
        if lo <= minute <= hi:
            return key
    return "76-90"


def model_points(match):
    """What the model itself would score under the core rules (result + exact + half-time)."""
    pr = getattr(match, "prediction", None)
    if pr is None or not match.played:
        return None
    pts = 0
    if pr.pick == match.result_letter:
        pts += POINTS["result"]
    if (pr.pred_home, pr.pred_away) == (match.home_goals, match.away_goals):
        pts += POINTS["exact"]
    ht = pr.detail.get("ht")
    if ht and match.ht_home is not None:
        if max(ht, key=ht.get) == result_letter(match.ht_home, match.ht_away):
            pts += POINTS["ht"]
    return pts


def score_pick(p, match, streak_before):
    """-> (points, breakdown list, correct_result bool). Pure function of pick + real result."""
    items = []
    res_ok = result_letter(p.home_goals, p.away_goals) == match.result_letter
    pr = getattr(match, "prediction", None)
    if res_ok:
        items.append(("Correct result", POINTS["result"]))
        if pr is not None:
            probs = {"H": pr.p_home, "D": pr.p_draw, "A": pr.p_away}
            if probs[match.result_letter] < 0.25:
                items.append(("Upset call (model gave it <25%)", POINTS["upset"]))
        streak = streak_before + 1
        if streak >= 3:
            items.append((f"Streak ×{streak}", min(streak - 2, 3)))
    if (p.home_goals, p.away_goals) == (match.home_goals, match.away_goals):
        items.append(("Exact score", POINTS["exact"]))
    if p.ht_result and match.ht_home is not None and p.ht_result == result_letter(match.ht_home, match.ht_away):
        items.append(("Half-time result", POINTS["ht"]))
    if p.red_card is not None and match.home_red is not None:
        real_red = (match.home_red or 0) + (match.away_red or 0) > 0
        if p.red_card == real_red:
            items.append(("Red-card call", POINTS["red_yes"] if real_red else POINTS["red_no"]))
    if p.yellows_over is not None and match.home_yellow is not None:
        if p.yellows_over == ((match.home_yellow or 0) + (match.away_yellow or 0) >= 4):
            items.append(("Yellow cards (3.5 line)", POINTS["yellows"]))
    if p.corners_over is not None and match.home_corners is not None:
        if p.corners_over == ((match.home_corners or 0) + (match.away_corners or 0) >= 10):
            items.append(("Corners (9.5 line)", POINTS["corners"]))
    goals = [e for e in match.events.all() if e.kind == MatchEvent.GOAL]
    if goals:  # event-level bonuses only when staff entered the goal events
        first = goals[0]
        if p.first_goal_window and p.first_goal_window == window_of(first.minute):
            items.append(("First-goal window", POINTS["first_window"]))
        if p.first_scorer and p.first_scorer.strip().lower() == first.player.strip().lower():
            items.append(("First scorer", POINTS["first_scorer"]))
    elif match.home_goals + match.away_goals == 0 and p.first_goal_window == "none":
        items.append(("First-goal window (no goal)", POINTS["first_window"]))
    return sum(v for _, v in items), items, res_ok


def recompute_user(user):
    """Re-score every settled pick of a user in kick-off order (streaks depend on order)."""
    picks = (UserPrediction.objects.filter(user=user, match__home_goals__isnull=False)
             .select_related("match", "match__prediction").prefetch_related("match__events").order_by("match__kickoff"))
    streak, best, exact, redhit, upset, beat = 0, 0, 0, False, False, 0
    week_ok = {}
    for p in picks:
        pts, items, ok = score_pick(p, p.match, streak)
        streak = streak + 1 if ok else 0
        best = max(best, streak)
        p.points, p.breakdown, p.settled = pts, [{"label": l, "pts": v} for l, v in items], True
        p.save(update_fields=["points", "breakdown", "settled"])
        exact += any(l == "Exact score" for l, _ in items)
        redhit |= any(l == "Red-card call" and v == POINTS["red_yes"] for l, v in items)
        upset |= any(l.startswith("Upset") for l, _ in items)
        mp = model_points(p.match)
        beat += pts - (mp or 0)
        wk = week_ok.setdefault((p.match.season_id, p.match.matchweek), [])
        wk.append(ok)
    n_all = UserPrediction.objects.filter(user=user).count()
    earned = set()
    n_settled = picks.count()
    if any(any(ok for ok in v) for v in week_ok.values()):
        earned.add("first_blood")
    if exact:
        earned.add("sniper")
    if exact >= 5:
        earned.add("oracle")
    if best >= 3:
        earned.add("hat_trick")
    if any(len(v) >= 5 and all(v) for v in week_ok.values()):
        earned.add("perfect_week")
    if upset:
        earned.add("upset_caller")
    if redhit:
        earned.add("card_shark")
    if n_all >= 20:
        earned.add("regular")
    if n_settled >= 10 and beat > 0:
        earned.add("beat_model")
    have = set(UserBadge.objects.filter(user=user).values_list("code", flat=True))
    UserBadge.objects.bulk_create([UserBadge(user=user, code=c) for c in earned - have])
    return {"streak": streak, "best_streak": best}


def settle_match(match):
    """Call after real results are recorded. Re-scores everyone who picked this match."""
    if not match.played:
        return 0
    users = {p.user for p in UserPrediction.objects.filter(match=match).select_related("user")}
    for u in users:
        recompute_user(u)
    return len(users)


def user_summary(user):
    picks = UserPrediction.objects.filter(user=user)
    settled = picks.filter(settled=True)
    pts = settled.aggregate(s=Sum("points"))["s"] or 0
    n = settled.count()
    correct = sum(1 for p in settled.select_related("match") if p.result_letter == p.match.result_letter)
    exact = sum(1 for p in settled.select_related("match")
                if (p.home_goals, p.away_goals) == (p.match.home_goals, p.match.away_goals))
    streak = 0
    for p in settled.select_related("match").order_by("-match__kickoff"):
        if p.result_letter == p.match.result_letter:
            streak += 1
        else:
            break
    return {"points": pts, "made": picks.count(), "settled": n, "correct": correct, "exact": exact, "streak": streak,
            "accuracy": (correct / n) if n else None, "level": level_for(pts)}
