"""Time-decayed, ridge-regularised Poisson ratings (Dixon-Coles style) + Dixon-Coles low-score fix.

log(lambda_home) = mu + home_adv + attack[home] + weak[away]
log(lambda_away) = mu +            attack[away] + weak[home]

`weak` is how much a side LEAKS (higher = worse defence). Observations are weighted by
0.5 ** (age_in_years / half_life) so recent seasons count more, and a ridge penalty pulls every
rating toward a prior (league average, or the "newly promoted" prior) - so a team we have
little/old data on is shrunk instead of trusted.
"""
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

MAXG = 10  # scoreline grid is 0..MAXG goals each


def decay_weights(dates, ref_date, half_life):
    age = (pd.Timestamp(ref_date) - dates).dt.days.to_numpy() / 365.25
    return 0.5 ** (np.clip(age, 0, None) / half_life)


def fit_counts(df, yh, ya, teams, ref_date, half_life, ridge, prior_a=None, prior_d=None):
    """Fit one count model (goals, yellows, corners ...). Returns dict with mu, home, a, d, n_eff."""
    d = df.dropna(subset=[yh, ya])
    idx = {t: i for i, t in enumerate(teams)}
    T = len(teams)
    hi = d["home"].map(idx).to_numpy()
    ai = d["away"].map(idx).to_numpy()
    y_h = d[yh].to_numpy(float)
    y_a = d[ya].to_numpy(float)
    w = decay_weights(d["date"], ref_date, half_life)
    pa = np.zeros(T) if prior_a is None else np.array([prior_a.get(t, 0.0) for t in teams])
    pd_ = np.zeros(T) if prior_d is None else np.array([prior_d.get(t, 0.0) for t in teams])

    def f(th):
        mu, h = th[0], th[1]
        a, dd = th[2:2 + T], th[2 + T:]
        lh = mu + h + a[hi] + dd[ai]
        la = mu + a[ai] + dd[hi]
        eh, ea = np.exp(lh), np.exp(la)
        nll = (w * (eh - y_h * lh)).sum() + (w * (ea - y_a * la)).sum()
        nll += ridge * (((a - pa) ** 2).sum() + ((dd - pd_) ** 2).sum())
        rh, ra = w * (eh - y_h), w * (ea - y_a)
        g = np.empty_like(th)
        g[0] = rh.sum() + ra.sum()
        g[1] = rh.sum()
        g[2:2 + T] = np.bincount(hi, rh, T) + np.bincount(ai, ra, T) + 2 * ridge * (a - pa)
        g[2 + T:] = np.bincount(ai, rh, T) + np.bincount(hi, ra, T) + 2 * ridge * (dd - pd_)
        return nll, g

    th0 = np.zeros(2 + 2 * T)
    th0[0] = np.log(max(np.average(np.r_[y_h, y_a]), 0.05))
    res = minimize(f, th0, jac=True, method="L-BFGS-B", options={"maxiter": 500})
    th = res.x
    n_eff = np.bincount(hi, w, T) + np.bincount(ai, w, T)
    return {
        "mu": th[0], "home": th[1],
        "a": dict(zip(teams, th[2:2 + T])), "d": dict(zip(teams, th[2 + T:])),
        "n_eff": dict(zip(teams, n_eff)),
    }


def lambdas(fit, home, away):
    ah, wh = fit["a"].get(home, 0.0), fit["d"].get(home, 0.0)
    aa, wa = fit["a"].get(away, 0.0), fit["d"].get(away, 0.0)
    return (np.exp(fit["mu"] + fit["home"] + ah + wa), np.exp(fit["mu"] + aa + wh))


def newcomer_prior(df):
    """Average attack / leakiness (log ratio vs league) of teams in their first season after
    arriving (not present the season before). Estimated from the training data only."""
    seasons = sorted(df["season"].unique())
    att, weak = [], []
    for s_prev, s in zip(seasons[:-1], seasons[1:]):
        prev = set(df.loc[df.season == s_prev, "home"])
        cur = df[df.season == s]
        lg = np.average(np.r_[cur.hg, cur.ag])
        for t in set(cur.home) - prev:
            gf = np.r_[cur.loc[cur.home == t, "hg"], cur.loc[cur.away == t, "ag"]].mean()
            ga = np.r_[cur.loc[cur.home == t, "ag"], cur.loc[cur.away == t, "hg"]].mean()
            att.append(np.log(max(gf, 0.3) / lg))
            weak.append(np.log(max(ga, 0.3) / lg))
    return (float(np.mean(att)), float(np.mean(weak))) if att else (0.0, 0.0)


def fit_rho(df, fit, ref_date, half_life):
    """1-D search for the Dixon-Coles dependence parameter on the low-scoring cells."""
    w = decay_weights(df["date"], ref_date, half_life)
    lh = np.array([lambdas(fit, h, a)[0] for h, a in zip(df.home, df.away)])
    la = np.array([lambdas(fit, h, a)[1] for h, a in zip(df.home, df.away)])
    hg, ag = df.hg.to_numpy(), df.ag.to_numpy()
    best, best_ll = 0.0, -np.inf
    for rho in np.linspace(-0.25, 0.1, 71):
        tau = np.ones(len(df))
        tau = np.where((hg == 0) & (ag == 0), 1 - lh * la * rho, tau)
        tau = np.where((hg == 0) & (ag == 1), 1 + lh * rho, tau)
        tau = np.where((hg == 1) & (ag == 0), 1 + la * rho, tau)
        tau = np.where((hg == 1) & (ag == 1), 1 - rho, tau)
        if (tau <= 0).any():
            continue
        ll = (w * np.log(tau)).sum()
        if ll > best_ll:
            best, best_ll = rho, ll
    return float(best)


def score_grid(lh, la, rho):
    """P(home goals = i, away goals = j), (MAXG+1)^2, with the Dixon-Coles correction."""
    k = np.arange(MAXG + 1)
    g = np.outer(poisson.pmf(k, lh), poisson.pmf(k, la))
    g[0, 0] *= 1 - lh * la * rho
    g[0, 1] *= 1 + lh * rho
    g[1, 0] *= 1 + la * rho
    g[1, 1] *= 1 - rho
    return g / g.sum()


def outcome_probs(grid):
    return float(np.tril(grid, -1).sum()), float(np.trace(grid)), float(np.triu(grid, 1).sum())


def pick_scoreline(grid, pick):
    """Most likely scoreline INSIDE the chosen outcome (0=home win, 1=draw, 2=away win)."""
    n = grid.shape[0]
    i, j = np.indices((n, n))
    mask = (i > j) if pick == 0 else (i == j) if pick == 1 else (i < j)
    flat = np.where(mask, grid, -1.0)
    a, b = np.unravel_index(np.argmax(flat), flat.shape)
    return int(a), int(b)
