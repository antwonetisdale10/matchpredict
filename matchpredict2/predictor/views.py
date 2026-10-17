import threading
import uuid
from collections import defaultdict

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.db import connection
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import importers, rewards
from .engine.pipeline import run_forecast
from .models import (BacktestResult, Match, MatchEvent, Player, Prediction, Season, Team, TeamProjection, TeamRating,
                     UserBadge, UserPrediction)

# ----------------------------------------------------------------------------- helpers


def season_matches(season):
    return (Match.objects.filter(season=season).select_related("home", "away", "prediction")
            .prefetch_related("events").order_by("kickoff", "id"))


def current_week(season):
    now = timezone.now() - timezone.timedelta(hours=3)
    m = Match.objects.filter(season=season, kickoff__gte=now).order_by("kickoff").first()
    if m:
        return m.matchweek
    last = Match.objects.filter(season=season).order_by("-kickoff").first()
    return last.matchweek if last else 1


def attach_picks(matches, user):
    matches = list(matches)
    if user.is_authenticated:
        mine = {p.match_id: p for p in UserPrediction.objects.filter(user=user, match__in=matches)}
        for m in matches:
            m.my_pick = mine.get(m.id)
    else:
        for m in matches:
            m.my_pick = None
    return matches


def get_team_by_slug(slug):
    for t in Team.objects.all():
        if t.slug == slug:
            return t
    raise Http404


def no_data(request):
    return render(request, "predictor/empty.html", status=200)


def studio_allowed(user):
    return user.is_authenticated and (user.is_staff or not settings.DATA_STUDIO_STAFF_ONLY)


# ----------------------------------------------------------------------------- browse


def home(request):
    season = request.season
    if not season:
        return no_data(request)
    week = current_week(season)
    wk = attach_picks(season_matches(season).filter(matchweek=week), request.user)
    preds = [m for m in season_matches(season) if getattr(m, "prediction", None)]
    upcoming = [m for m in preds if not m.locked] or preds
    bold = sorted(upcoming, key=lambda m: -m.prediction.confidence)[:3]
    goals = sorted(upcoming, key=lambda m: -(m.prediction.xg_home + m.prediction.xg_away))[:3]
    tight = sorted(upcoming, key=lambda m: abs(m.prediction.p_home - m.prediction.p_away))[:3]
    proj = list(TeamProjection.objects.filter(season=season).select_related("team").order_by("exp_rank"))
    board = leaderboard_rows(season)[:5]
    prov = preds[0].prediction.model_version if preds else ""
    return render(request, "predictor/home.html", {
        "week": week, "matches": wk, "bold": bold, "goals": goals, "tight": tight, "top": proj[:6], "bottom": proj[-3:],
        "board": board, "provenance": prov, "n_matches": len(preds),
        "n_played": sum(1 for m in preds if m.played), "n_users": User.objects.count(),
    })


def matches(request):
    season = request.season
    if not season:
        return no_data(request)
    weeks = list(Match.objects.filter(season=season).values_list("matchweek", flat=True).distinct().order_by("matchweek"))
    team = None
    if request.GET.get("team"):
        team = get_team_by_slug(request.GET["team"])
    week = request.GET.get("week")
    qs = season_matches(season)
    if team:
        qs = qs.filter(Q(home=team) | Q(away=team))
    if week and week.isdigit():
        week = int(week)
        qs = qs.filter(matchweek=week)
    elif team:
        week = None
    else:
        week = current_week(season)
        qs = qs.filter(matchweek=week)
    items = attach_picks(qs, request.user)
    teams = Team.objects.filter(Q(home_matches__season=season) | Q(away_matches__season=season)).distinct().order_by("name")
    proj = TeamProjection.objects.filter(season=season, team=team).first() if team else None
    now_week = current_week(season)
    return render(request, "predictor/matches.html", {
        "items": items, "weeks": weeks, "week": week, "team": team, "teams": teams, "proj": proj, "now_week": now_week,
        "by_week": bool(week),
    })


def h2h(match, limit=6):
    qs = (Match.objects.filter(home_goals__isnull=False, season__start_year__lt=match.season.start_year)
          .filter(Q(home=match.home, away=match.away) | Q(home=match.away, away=match.home))
          .select_related("home", "away", "season").order_by("-kickoff")[:limit])
    rows, w, d, l = [], 0, 0, 0
    for m in qs:
        gf, ga = (m.home_goals, m.away_goals) if m.home_id == match.home_id else (m.away_goals, m.home_goals)
        w, d, l = w + (gf > ga), d + (gf == ga), l + (gf < ga)
        rows.append(m)
    return rows, (w, d, l)


def pick_context(request, match):
    pick = None
    if request.user.is_authenticated:
        pick = UserPrediction.objects.filter(user=request.user, match=match).first()
    players = (Player.objects.filter(season=match.season, team__in=[match.home, match.away]).exclude(pos="GK")
               .select_related("team").order_by("team__name", "name"))
    return {"match": match, "pick": pick, "players": players, "windows": UserPrediction.WINDOWS, "locked": match.locked}


def nudged_timeline(events):
    """Space same-side markers at least ~4% apart so pins never overlap."""
    out, last = [], {"home": -99.0, "away": -99.0}
    for e in sorted(events, key=lambda e: e["pos"]):
        pos = max(e["pos"], last[e["side"]] + 4.2)
        last[e["side"]] = pos
        out.append({**e, "pos": min(pos, 97.0)})
    return out


def match_detail(request, pk):
    match = get_object_or_404(Match.objects.select_related("home", "away", "season", "prediction").prefetch_related("events"), pk=pk)
    pred = getattr(match, "prediction", None)
    rows, record = h2h(match)
    ratings = {r.team_id: r for r in TeamRating.objects.filter(season=match.season, team__in=[match.home, match.away])}
    actual = None
    if match.played:
        actual = {"events": list(match.events.all()), "model_points": rewards.model_points(match)}
    ctx = {"m": match, "p": pred, "d": pred.detail if pred else {}, "tl": nudged_timeline(pred.detail["timeline"]) if pred else [],
           "bins": list(zip(pred.detail["goal_bins"]["labels"], pred.detail["goal_bins"]["any"])) if pred else [], "h2h": rows, "record": record,
           "rh": ratings.get(match.home_id), "ra": ratings.get(match.away_id), "actual": actual,
           "proj": {p.team_id: p for p in TeamProjection.objects.filter(season=match.season, team__in=[match.home, match.away])}}
    ctx.update(pick_context(request, match))
    prev_next = Match.objects.filter(season=match.season, matchweek=match.matchweek).order_by("kickoff", "id")
    ctx["siblings"] = prev_next.select_related("home", "away")
    return render(request, "predictor/match.html", ctx)


def pick_form(request, pk):
    """GET -> HTML partial for the sheet; POST -> save the pick."""
    match = get_object_or_404(Match.objects.select_related("home", "away", "prediction", "season"), pk=pk)
    if request.method == "GET":
        ctx = pick_context(request, match)
        ctx["p"] = getattr(match, "prediction", None)
        return render(request, "predictor/partials/pick_form.html", ctx)
    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "error": "Sign in to make a prediction.", "login": True}, status=401)
    if match.locked:
        return JsonResponse({"ok": False, "error": "Kick-off has passed - predictions are closed for this match."}, status=400)
    f = request.POST
    try:
        hg, ag = int(f["home_goals"]), int(f["away_goals"])
        assert 0 <= hg <= 15 and 0 <= ag <= 15
    except (KeyError, ValueError, AssertionError):
        return JsonResponse({"ok": False, "error": "Enter a score between 0 and 15."}, status=400)

    def tri(name, yes, no):
        v = f.get(name, "")
        return True if v == yes else False if v == no else None

    win = f.get("first_goal_window", "")
    if win not in dict(UserPrediction.WINDOWS):
        win = ""
    ht = f.get("ht_result", "")
    if ht not in ("H", "D", "A"):
        ht = ""
    if win == "none" and hg + ag > 0:
        return JsonResponse({"ok": False, "error": "You picked 'No goal' but predicted a score with goals."}, status=400)
    obj, created = UserPrediction.objects.update_or_create(user=request.user, match=match, defaults={
        "home_goals": hg, "away_goals": ag, "ht_result": ht, "first_scorer": f.get("first_scorer", "")[:80],
        "first_goal_window": win, "red_card": tri("red_card", "yes", "no"), "yellows_over": tri("yellows_over", "over", "under"),
        "corners_over": tri("corners_over", "over", "under")})
    rewards.recompute_user(request.user)
    n = UserPrediction.objects.filter(user=request.user).count()
    return JsonResponse({"ok": True, "created": created, "pick": f"{hg}–{ag}", "count": n,
                         "message": "Locked in." if created else "Pick updated."})


def table(request):
    season = request.season
    if not season:
        return no_data(request)
    rows = list(TeamProjection.objects.filter(season=season).select_related("team").order_by("exp_rank"))
    ratings = {r.team_id: r for r in TeamRating.objects.filter(season=season)}
    for i, r in enumerate(rows, 1):
        r.pos, r.gd, r.rating = i, r.exp_gf - r.exp_ga, ratings.get(r.team_id)
    n = len(rows)
    first = Prediction.objects.filter(match__season=season).first()
    return render(request, "predictor/table.html", {"rows": rows, "n": n, "pred": first})


def teams(request):
    season = request.season
    if not season:
        return no_data(request)
    rows = list(TeamRating.objects.filter(season=season).select_related("team").order_by("-overall"))
    proj = {p.team_id: p for p in TeamProjection.objects.filter(season=season)}
    for r in rows:
        r.proj = proj.get(r.team_id)
    mx = max((r.attack for r in rows), default=1)
    md = max((r.defence for r in rows), default=1)
    return render(request, "predictor/teams.html", {"rows": rows, "max_att": mx, "max_def": md})


def team_detail(request, slug):
    season = request.season
    team = get_team_by_slug(slug)
    if not season:
        return no_data(request)
    rating = TeamRating.objects.filter(season=season, team=team).first()
    proj = TeamProjection.objects.filter(season=season, team=team).first()
    ratings = {r.team_id: r for r in TeamRating.objects.filter(season=season)}
    order = sorted(ratings.values(), key=lambda r: -r.overall)
    rank_of = {r.team_id: i + 1 for i, r in enumerate(order)}
    fixtures = []
    for m in attach_picks(season_matches(season).filter(Q(home=team) | Q(away=team)), request.user):
        p = getattr(m, "prediction", None)
        home = m.home_id == team.id
        opp = m.away if home else m.home
        win = (p.p_home if home else p.p_away) if p else None
        letter = None
        if p:
            letter = {"H": "W" if home else "L", "A": "L" if home else "W", "D": "D"}[p.pick]
        fixtures.append({"m": m, "opp": opp, "home": home, "p": p, "win": win, "letter": letter,
                         "diff": rank_of.get(opp.id, 10), "score": (f"{p.pred_home}–{p.pred_away}" if p else "")})
    rk = [f["diff"] for f in fixtures]
    # season-long player projections for this team
    xg, book = defaultdict(lambda: [0.0, 0.0, "", 0]), defaultdict(lambda: [0.0, ""])
    for f in fixtures:
        if not f["p"]:
            continue
        d, side = f["p"].detail, ("home" if f["home"] else "away")
        xgt = f["p"].xg_home if f["home"] else f["p"].xg_away
        for s in d["scorers"][side]:
            xg[s["name"]][0] += xgt * s["share"]
            xg[s["name"]][2] = s["pos"]
        for c in d["cards"][side]:
            book[c["name"]][0] += c["p_booked"]
            book[c["name"]][1] = c["pos"]
    scorers = sorted(([n, v[0], v[2]] for n, v in xg.items()), key=lambda x: -x[1])[:5]
    booked = sorted(([n, v[0], v[1]] for n, v in book.items()), key=lambda x: -x[1])[:5]
    return render(request, "predictor/team.html", {
        "team": team, "rating": rating, "proj": proj, "fixtures": fixtures, "rank": rank_of.get(team.id),
        "hard_run": sorted(fixtures, key=lambda f: f["diff"])[:3], "scorers": scorers, "booked": booked,
        "avg_diff": (sum(rk) / len(rk)) if rk else 0, "n_teams": len(ratings),
    })


def players(request):
    season = request.season
    if not season:
        return no_data(request)
    xg, book = {}, {}
    for pr in Prediction.objects.filter(match__season=season).select_related("match__home", "match__away"):
        for side, xgt, team in (("home", pr.xg_home, pr.match.home), ("away", pr.xg_away, pr.match.away)):
            for s in pr.detail["scorers"][side]:
                k = (s["name"], team.name)
                e = xg.setdefault(k, [0.0, s["pos"], 0.0])
                e[0] += xgt * s["share"]
                e[2] += s["p_anytime"]
            for c in pr.detail["cards"][side]:
                k = (c["name"], team.name)
                e = book.setdefault(k, [0.0, c["pos"]])
                e[0] += c["p_booked"]
    boot = sorted(([n, t, v[0], v[1], v[2]] for (n, t), v in xg.items()), key=lambda x: -x[2])[:15]
    cards = sorted(([n, t, v[0], v[1]] for (n, t), v in book.items()), key=lambda x: -x[2])[:10]
    return render(request, "predictor/players.html", {"boot": boot, "cards": cards})


# ----------------------------------------------------------------------------- rewards


def leaderboard_rows(season, week=None):
    qs = UserPrediction.objects.filter(settled=True, match__season=season)
    if week:
        qs = qs.filter(match__matchweek=week)
    agg = qs.values("user", "user__username").annotate(points=Sum("points"), n=Count("id")).order_by("-points", "n")
    out = []
    for a in agg:
        picks = list(qs.filter(user_id=a["user"]).select_related("match"))
        correct = sum(1 for p in picks if p.result_letter == p.match.result_letter)
        exact = sum(1 for p in picks if (p.home_goals, p.away_goals) == (p.match.home_goals, p.match.away_goals))
        total = rewards.user_summary(User(id=a["user"]))["points"] if not week else a["points"]
        out.append({"user": a["user__username"], "points": a["points"], "n": a["n"], "correct": correct, "exact": exact,
                    "level": rewards.level_for(total)["name"]})
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


def leaderboard(request):
    season = request.season
    if not season:
        return no_data(request)
    week = request.GET.get("week")
    week = int(week) if week and week.isdigit() else None
    rows = leaderboard_rows(season, week)
    played = [m for m in season_matches(season) if m.played and getattr(m, "prediction", None)]
    if week:
        played = [m for m in played if m.matchweek == week]
    mp = sum(rewards.model_points(m) or 0 for m in played)
    weeks = list(Match.objects.filter(season=season, home_goals__isnull=False).values_list("matchweek", flat=True)
                 .distinct().order_by("matchweek"))
    return render(request, "predictor/leaderboard.html", {
        "rows": rows, "week": week, "weeks": weeks, "model_pts": mp, "model_n": len(played),
        "model_avg": (mp / len(played)) if played else None, "levels": rewards.LEVELS, "points": rewards.POINTS,
        "badges": rewards.BADGES,
    })


@login_required
def profile(request):
    s = rewards.user_summary(request.user)
    picks = list(UserPrediction.objects.filter(user=request.user).select_related("match__home", "match__away", "match__prediction"))
    have = {b.code: b for b in UserBadge.objects.filter(user=request.user)}
    badges = [{"code": c, "name": n, "desc": d, "got": c in have} for c, (n, d) in rewards.BADGES.items()]
    pending = [p for p in picks if not p.settled]
    done = [p for p in picks if p.settled]
    ring = round(s["level"]["progress"] * 100)
    to_next = (s["level"]["next_at"] - s["points"]) if s["level"]["next_at"] else 0
    return render(request, "predictor/profile.html", {"s": s, "to_next": to_next, "badges": badges, "pending": pending, "done": done,
                                                       "ring": ring, "levels": rewards.LEVELS})


def signup(request):
    form = UserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect(request.GET.get("next") or "home")
    return render(request, "registration/signup.html", {"form": form})


# ----------------------------------------------------------------------------- accuracy


def accuracy(request):
    rows = list(BacktestResult.objects.select_related("season").order_by("season__start_year"))
    agg = None
    if rows:
        n = sum(r.n_matches for r in rows)

        def w(key):
            return sum(r.metrics[key] * r.n_matches for r in rows) / n

        cal = defaultdict(lambda: [0, 0.0, 0.0])
        for r in rows:
            for c in r.metrics["calibration"]:
                k = (c["lo"], c["hi"])
                cal[k][0] += c["n"]
                cal[k][1] += c["pred"] * c["n"]
                cal[k][2] += c["actual"] * c["n"]
        calib = [{"lo": k[0], "hi": k[1], "n": v[0], "pred": v[1] / v[0], "actual": v[2] / v[0]} for k, v in sorted(cal.items()) if v[0]]
        agg = {"n": n, "seasons": len(rows), "accuracy": w("accuracy"), "baseline": w("baseline_accuracy"),
               "log_loss": w("log_loss"), "base_ll": w("baseline_log_loss"), "rps": w("rps"), "base_rps": w("baseline_rps"),
               "exact": w("exact_score"), "goals_mae": w("total_goals_mae"), "calib": calib}
    season = request.season
    pred = Prediction.objects.filter(match__season=season).first() if season else None
    train_note = pred.model_version if pred else None
    return render(request, "predictor/accuracy.html", {"rows": rows, "agg": agg, "train_note": train_note,
                                                       "n_played_target": Match.objects.filter(season=season, home_goals__isnull=False).count() if season else 0})


# ----------------------------------------------------------------------------- data studio

JOBS = {}


def studio_required(view):
    def wrapper(request, *a, **k):
        if not request.user.is_authenticated:
            return redirect(f"{settings.LOGIN_URL}?next={request.path}")
        if not studio_allowed(request.user):
            return HttpResponseForbidden("Data Studio is staff-only on this server.")
        return view(request, *a, **k)
    wrapper.__name__ = view.__name__
    return wrapper


def _year(request, field="year"):
    try:
        y = int(request.POST[field])
        assert 1990 <= y <= 2100
        return y
    except (KeyError, ValueError, AssertionError):
        raise ValueError("Pick a valid season start year (e.g. 2027 for 2027/28).")


@studio_required
def studio(request):
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "add_season":
                r = importers.import_season_csv(request.FILES["file"], _year(request), replace=bool(request.POST.get("replace")))
                messages.success(request, f"{r['season']}: " + ("already there (tick replace to overwrite)." if r["skipped"] else f"imported {r['created']} matches."))
            elif action == "fixtures":
                r = importers.import_fixtures_csv(request.FILES["file"], _year(request), replace=bool(request.POST.get("replace")))
                messages.success(request, f"{r['season']}: {r['created']} fixtures loaded (scores ignored).")
            elif action == "generate":
                names = [t.strip() for t in request.POST.get("teams", "").replace(",", "\n").splitlines() if t.strip()]
                if len(names) < 4 or len(names) % 2:
                    raise ValueError("Need an even number of teams (at least 4).")
                r = importers.import_generated_fixtures(names, _year(request), replace=bool(request.POST.get("replace")))
                messages.success(request, f"{r['season']}: generated {r['created']} fixtures for {len(names)} teams.")
            elif action == "players":
                r = importers.import_players_csv(request.FILES["file"], _year(request))
                messages.success(request, f"{r['season']}: {r['players']} players loaded.")
            elif action == "results":
                y = _year(request)
                r = importers.import_results_csv(request.FILES["file"], y)
                settled = sum(rewards.settle_match(m) for m in Match.objects.filter(season__start_year=y, home_goals__isnull=False).filter(user_picks__isnull=False).distinct())
                messages.success(request, f"{r['season']}: {r['updated']} results recorded, {settled} picks re-scored."
                                 + (f" Couldn't match: {', '.join(r['missing'][:5])}" if r["missing"] else ""))
            elif action == "predict":
                y = _year(request)
                if not Match.objects.filter(season__start_year=y).exists():
                    raise ValueError("That season has no fixtures yet.")
                jid = uuid.uuid4().hex[:8]
                JOBS[jid] = {"state": "running", "done": 0, "total": Match.objects.filter(season__start_year=y).count(), "year": y}

                def work():
                    try:
                        res = run_forecast(y, progress=lambda i, n: JOBS[jid].update(done=i), nsim=8000)
                        JOBS[jid].update(state="done", done=JOBS[jid]["total"], msg=res["version"])
                    except Exception as e:  # noqa: BLE001
                        JOBS[jid].update(state="error", msg=str(e))
                    finally:
                        connection.close()

                threading.Thread(target=work, daemon=True).start()
                request.session["job"] = jid
                messages.info(request, "Predicting… this takes about half a minute.")
        except KeyError:
            messages.error(request, "Choose a file first.")
        except Exception as e:  # noqa: BLE001
            messages.error(request, str(e))
        return redirect("studio")
    all_seasons = list(Season.objects.annotate(n=Count("matches"), played=Count("matches", filter=Q(matches__home_goals__isnull=False)),
                                               preds=Count("matches__prediction")).order_by("start_year"))
    latest = next((s for s in reversed(all_seasons) if s.preds), None)
    default_teams = ""
    if latest:
        default_teams = "\n".join(Team.objects.filter(Q(home_matches__season=latest) | Q(away_matches__season=latest))
                                  .distinct().order_by("name").values_list("name", flat=True))
    last_year = max((s.start_year for s in all_seasons), default=2025)
    unplayed = (Match.objects.filter(season=request.season, home_goals__isnull=True, kickoff__lte=timezone.now())
                .select_related("home", "away").order_by("kickoff")[:30] if request.season else [])
    return render(request, "predictor/studio.html", {"all_seasons": all_seasons, "default_teams": default_teams,
                                                      "next_year": last_year + 1, "job": request.session.get("job"), "unplayed": unplayed})


@studio_required
def job_status(request, jid):
    return JsonResponse(JOBS.get(jid, {"state": "unknown"}))


@studio_required
def record_result(request, pk):
    match = get_object_or_404(Match.objects.select_related("home", "away"), pk=pk)
    fields = ["home_goals", "away_goals", "ht_home", "ht_away", "home_yellow", "away_yellow", "home_red", "away_red",
              "home_corners", "away_corners", "home_shots", "away_shots", "home_sot", "away_sot", "home_fouls", "away_fouls"]
    if request.method == "POST":
        try:
            for f in fields:
                v = request.POST.get(f, "").strip()
                setattr(match, f, int(v) if v != "" else None)
            if match.home_goals is None or match.away_goals is None:
                raise ValueError("Full-time score is required.")
            match.save()
            match.events.all().delete()
            for line in request.POST.get("events", "").splitlines():
                parts = line.strip().split(None, 3)  # "23 H Haaland Foden"
                if len(parts) >= 3 and parts[0].isdigit() and parts[1].upper() in ("H", "A"):
                    team = match.home if parts[1].upper() == "H" else match.away
                    MatchEvent.objects.create(match=match, minute=int(parts[0]), kind=MatchEvent.GOAL, team=team,
                                              player=parts[2], assist=parts[3] if len(parts) > 3 else "")
            n = rewards.settle_match(match)
            messages.success(request, f"Result saved. {n} user pick(s) scored.")
            return redirect("studio")
        except ValueError as e:
            messages.error(request, str(e))
    return render(request, "predictor/record_result.html", {"m": match, "events": match.events.all()})


def sample_csv(request, kind):
    from django.http import HttpResponse
    samples = {
        "fixtures": "matchweek,date,time,home,away\n1,2027-08-14,15:00,Arsenal,Chelsea\n1,2027-08-14,17:30,Liverpool,Man City\n",
        "season": "Date,HomeTeam,AwayTeam,FTHG,FTAG,HTHG,HTAG,Referee,HS,AS,HST,AST,HF,AF,HC,AC,HY,AY,HR,AR\n"
                  "2025-08-16,Arsenal,Chelsea,2,1,1,0,M Oliver,14,9,6,3,10,12,7,4,1,2,0,0\n",
        "players": "name,first_name,second_name,club,pos,start_price,g_prev,a_prev,min_prev,pen_order,fk_order,y_prev,r_prev\n"
                   "Saka,Bukayo,Saka,Arsenal,MID,10.0,10,8,2900,1,,3,0\n",
    }
    if kind not in samples:
        raise Http404
    r = HttpResponse(samples[kind], content_type="text/csv")
    r["Content-Disposition"] = f'attachment; filename="sample_{kind}.csv"'
    return r
