"""Walk-forward backtest: predict season S using ONLY seasons before S (minus `gap`).

gap=1 mimics the real 2026/27 situation, with one season hidden - harder than the live forecast, so a conservative estimate.
"""
import numpy as np
import pandas as pd

from . import ratings as R
from .data import season_frame, training_frame

HALF_LIFE, RIDGE = 1.5, 8.0  # tuned by walk-forward grid search (tools/tune.py)


def newcomer_teams(df, teams_in_target, min_recent=10):
    last = sorted(df["season"].unique())[-2:]
    recent = df[df.season.isin(last)]
    counts = pd.concat([recent.home, recent.away]).value_counts()
    return [t for t in teams_in_target if counts.get(t, 0) < min_recent]


def fit_goal_model(df, target_teams, ref_date, half_life=HALF_LIFE, ridge=RIDGE):
    teams = sorted(set(df.home) | set(df.away) | set(target_teams))
    pa_new, pw_new = R.newcomer_prior(df)
    new = newcomer_teams(df, target_teams)
    prior_a = {t: pa_new for t in new}
    prior_d = {t: pw_new for t in new}
    fit = R.fit_counts(df, "hg", "ag", teams, ref_date, half_life, ridge, prior_a, prior_d)
    fit["rho"] = R.fit_rho(df, fit, ref_date, half_life)
    fit["newcomers"] = new
    fit["prior"] = (pa_new, pw_new)
    return fit


def rps(p, outcome):
    """Ranked probability score for ordered outcomes (H, D, A)."""
    cp, co = np.cumsum(p), np.cumsum(outcome)
    return float(((cp - co) ** 2)[:-1].sum() / 2)


def backtest_season(season_year, gap=1, half_life=HALF_LIFE, ridge=RIDGE):
    train = training_frame(season_year, gap)
    test = season_frame(season_year)
    ref = pd.Timestamp(year=season_year, month=8, day=1)
    fit = fit_goal_model(train, sorted(set(test.home) | set(test.away)), ref, half_life, ridge)
    base = np.array([(train.hg > train.ag).mean(), (train.hg == train.ag).mean(), (train.hg < train.ag).mean()])
    rows = []
    for r in test.itertuples():
        lh, la = R.lambdas(fit, r.home, r.away)
        g = R.score_grid(lh, la, fit["rho"])
        ph, pdw, pa = R.outcome_probs(g)
        p = np.array([ph, pdw, pa])
        o = np.array([r.hg > r.ag, r.hg == r.ag, r.hg < r.ag], float)
        k = int(np.argmax(o))
        pick = int(np.argmax(p))
        i, j = R.pick_scoreline(g, pick)
        tot = np.add.outer(np.arange(R.MAXG + 1), np.arange(R.MAXG + 1))
        rows.append({
            "hit": pick == k, "ll": -np.log(max(p[k], 1e-9)), "ll_base": -np.log(base[k]), "rps": rps(p, o),
            "rps_base": rps(base, o), "exact": (i == r.hg and j == r.ag),
            "o25": float(g[tot > 2.5].sum()), "o25_act": float(r.hg + r.ag > 2.5),
            "btts": float(g[1:, 1:].sum()), "btts_act": float(r.hg > 0 and r.ag > 0),
            "xg_err": abs(lh + la - (r.hg + r.ag)), "p_pick": float(p[pick]), "home_pick": pick == 0,
            "base_hit": int(np.argmax(base)) == k,
        })
    m = pd.DataFrame(rows)
    # calibration: bucket the model's top-pick probability
    bins = [0.3, 0.4, 0.5, 0.6, 0.7, 1.01]
    m["bin"] = pd.cut(m.p_pick, bins, right=False)
    cal = [{"lo": float(b.left), "hi": float(min(b.right, 1.0)), "n": int(len(g)), "pred": float(g.p_pick.mean()),
            "actual": float(g.hit.mean())} for b, g in m.groupby("bin", observed=True)]
    return {
        "n": len(m), "accuracy": float(m.hit.mean()), "baseline_accuracy": float(m.base_hit.mean()),
        "log_loss": float(m.ll.mean()), "baseline_log_loss": float(m.ll_base.mean()),
        "rps": float(m.rps.mean()), "baseline_rps": float(m.rps_base.mean()),
        "exact_score": float(m.exact.mean()), "brier_over25": float(((m.o25 - m.o25_act) ** 2).mean()),
        "brier_btts": float(((m.btts - m.btts_act) ** 2).mean()), "total_goals_mae": float(m.xg_err.mean()),
        "calibration": cal,
    }
