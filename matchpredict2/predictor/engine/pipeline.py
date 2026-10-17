"""Glue: DB fixtures -> forecasts -> saved Prediction / TeamRating / TeamProjection rows."""
import numpy as np
import pandas as pd
from django.db import transaction

from predictor.models import BacktestResult, Match, Player, Prediction, Season, TeamProjection, TeamRating

from . import ratings as R
from .backtest import backtest_season
from .simulate import MODEL_VERSION, Forecaster

PLAYER_COLS = {"team__name": "club", "name": "name", "pos": "pos", "start_price": "start_price", "goals_prev": "g_prev",
               "assists_prev": "a_prev", "minutes_prev": "min_prev", "pen_order": "pen_order", "fk_order": "fk_order",
               "yellows_prev": "y_prev", "reds_prev": "r_prev"}


def players_frame(season):
    rows = Player.objects.filter(season=season).values(*PLAYER_COLS)
    return pd.DataFrame(list(rows)).rename(columns=PLAYER_COLS) if rows else None


def project_season(fixtures, grids, nsim=4000, seed=7):
    """Play the whole season `nsim` times from the per-match score distributions."""
    rng = np.random.default_rng(seed)
    teams = sorted({m.home.name for m in fixtures} | {m.away.name for m in fixtures})
    ti = {t: i for i, t in enumerate(teams)}
    T = len(teams)
    pts, gf, ga = (np.zeros((nsim, T)) for _ in range(3))
    for m, g in zip(fixtures, grids):
        cdf = np.cumsum(g.ravel())
        cdf[-1] = 1.0
        idx = np.searchsorted(cdf, rng.random(nsim))
        h, a = idx // g.shape[0], idx % g.shape[0]
        i, j = ti[m.home.name], ti[m.away.name]
        pts[:, i] += np.where(h > a, 3, np.where(h == a, 1, 0))
        pts[:, j] += np.where(a > h, 3, np.where(h == a, 1, 0))
        gf[:, i] += h
        gf[:, j] += a
        ga[:, i] += a
        ga[:, j] += h
    # rank by points, then goal difference, then goals for (tiny noise breaks exact ties)
    key = pts * 1e6 + (gf - ga) * 1e3 + gf + rng.random((nsim, T)) * 0.1
    rank = (-key).argsort(1).argsort(1) + 1
    out = {}
    for t, i in ti.items():
        r = rank[:, i]
        dist = np.bincount(r, minlength=T + 1)[1:] / nsim
        out[t] = {"exp_points": float(pts[:, i].mean()), "exp_gf": float(gf[:, i].mean()), "exp_ga": float(ga[:, i].mean()),
                  "exp_rank": float(r.mean()), "p_title": float((r == 1).mean()), "p_top4": float((r <= 4).mean()),
                  "p_relegation": float((r > T - 3).mean()), "rank_dist": [round(float(x), 4) for x in dist]}
    return out


def run_forecast(target_year, progress=None, nsim=20000):
    """Predict every fixture of `target_year` using ONLY earlier seasons."""
    season = Season.objects.get(start_year=target_year)
    fixtures = list(Match.objects.filter(season=season).select_related("home", "away").order_by("kickoff", "id"))
    if not fixtures:
        raise ValueError(f"{season.label} has no fixtures yet - upload or generate a fixture list first")
    teams = sorted({m.home.name for m in fixtures} | {m.away.name for m in fixtures})
    fc = Forecaster(target_year).fit(teams, players_frame(season))
    train = fc.train
    version = (f"{MODEL_VERSION} | trained on {int(train.season.min())}/{str(int(train.season.min()) + 1)[-2:]}"
               f"-{int(train.season.max())}/{str(int(train.season.max()) + 1)[-2:]} ({len(train)} matches)")
    # the guard that matters: nothing from the target season (or later) is in the training data
    assert train["season"].max() < target_year

    from predictor.models import Team
    tmap = {t.name: t for t in Team.objects.filter(name__in=teams)}
    squads = {t: fc.squad(t) for t in teams}
    rt = fc.ratings_table()
    preds, grids = [], []
    for n, m in enumerate(fixtures):
        fields, detail = fc.forecast(m.home.name, m.away.name, seed=target_year * 10_000 + m.id, nsim=nsim, squads=squads)
        grids.append(fc.last_grid)
        preds.append(Prediction(match=m, model_version=version, detail=detail, **fields))
        if progress and n % 20 == 0:
            progress(n + 1, len(fixtures))
    proj = project_season(fixtures, grids)
    with transaction.atomic():  # short write window so the site stays responsive
        Prediction.objects.filter(match__season=season).delete()
        TeamRating.objects.filter(season=season).delete()
        TeamProjection.objects.filter(season=season).delete()
        TeamRating.objects.bulk_create([
            TeamRating(season=season, team=tmap[t], attack=rt[t]["attack"], defence=rt[t]["defence"],
                       overall=rt[t]["attack"] * rt[t]["defence"], history_weight=rt[t]["n_eff"], newcomer=rt[t]["newcomer"],
                       detail={k: rt[t][k] for k in ("yellow", "corners", "shots")}) for t in teams])
        Prediction.objects.bulk_create(preds)
        TeamProjection.objects.bulk_create([TeamProjection(season=season, team=tmap[t], **v) for t, v in proj.items()])
    return {"season": season.label, "matches": len(preds), "version": version, "newcomers": fc.goal["newcomers"]}


def run_backtest(first=2015, last=None, gap=1):
    last = last or Season.objects.filter(is_complete=True).order_by("-start_year").first().start_year
    done = []
    for y in range(first, last + 1):
        try:
            season = Season.objects.get(start_year=y)
            res = backtest_season(y, gap=gap)
        except Exception:
            continue
        BacktestResult.objects.update_or_create(
            season=season, defaults={"model_version": f"{MODEL_VERSION} gap={gap}", "n_matches": res["n"], "metrics": res})
        done.append(y)
    return done
