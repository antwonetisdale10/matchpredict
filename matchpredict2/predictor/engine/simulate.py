"""Match forecaster: score distribution + a Monte-Carlo of everything else that happens in a game.

What is learned from the CSVs:   goals (attack/defence/home edge), half-time split of goals,
                                 yellows, reds, corners, shots, shots on target, fouls.
What is an explicit PRIOR (the CSVs have no minutes / scorers / penalties):
   * in-half goal-timing shape (goals get a bit more frequent as each half goes on)
   * stoppage-time length, card timing shape, penalty base rate (~0.28 per game)
   * who scores / is booked  -> last season's player numbers (see players.py)
These priors are listed in the README so nobody mistakes them for fitted values.
"""
import numpy as np
import pandas as pd
from scipy.stats import beta as beta_dist
from scipy.stats import nbinom

from . import ratings as R
from .backtest import HALF_LIFE, RIDGE, fit_goal_model
from .data import training_frame
from .players import league_priors, squad_table

MODEL_VERSION = "dixon-coles-mc-v1"
NSIM = 20000
MAXG_SIM = 10
B1, B2 = 0.45, 0.75   # goal-intensity slope inside 1st / 2nd half (prior)
S1, S2 = 2.0, 5.0     # average stoppage minutes 1st / 2nd half (prior)
PEN_BASE = 0.28       # penalties awarded per Premier League game (prior)
BIN_LABELS = ["0-15", "15-30", "30-45+", "45-60", "60-75", "75-90+"]
SIDE = {  # name: (home col, away col, ridge)
    "yellow": ("hy", "ay", 8.0), "red": ("hr", "ar", 40.0), "corners": ("hc", "ac", 8.0),
    "shots": ("hs", "as_", 8.0), "sot": ("hst", "ast", 8.0), "fouls": ("hf", "af", 8.0),
}


def _inv(v, b):
    return (-1 + np.sqrt(1 + 2 * b * v * (1 + b / 2))) / b


def sample_clock(rng, shape, p1):
    first = rng.random(shape) < p1
    u = rng.random(shape)
    return np.where(first, _inv(u, B1) * (45 + S1), 45 + _inv(u, B2) * (45 + S2)), first


def quantile_clock(q, p1):
    if q <= p1:
        return _inv(q / p1, B1) * (45 + S1), True
    return 45 + _inv((q - p1) / (1 - p1), B2) * (45 + S2), False


def label_minute(m, first):
    if first and m > 45:
        return f"45+{max(1, int(round(m - 45)))}'"
    if not first and m > 90:
        return f"90+{max(1, int(round(m - 90)))}'"
    return f"{max(1, int(round(m)))}'"


def bin_of(m, first):
    return np.where(first, np.where(m <= 15, 0, np.where(m <= 30, 1, 2)), np.where(m <= 60, 3, np.where(m <= 75, 4, 5)))


def r4(x):
    return float(np.round(x, 4))


class Forecaster:
    def __init__(self, target_year, gap=0, half_life=HALF_LIFE, ridge=RIDGE):
        self.year, self.gap, self.hl, self.ridge = target_year, gap, half_life, ridge

    # ------------------------------------------------------------------ fitting
    def fit(self, target_teams, players=None):
        self.train = train = training_frame(self.year, self.gap)
        self.ref = pd.Timestamp(year=self.year, month=8, day=1)
        self.teams_all = sorted(set(train.home) | set(train.away) | set(target_teams))
        self.goal = fit_goal_model(train, list(target_teams), self.ref, self.hl, self.ridge)
        self.side = {k: R.fit_counts(train, yh, ya, self.teams_all, self.ref, self.hl, rg)
                     for k, (yh, ya, rg) in SIDE.items()}
        w = R.decay_weights(train["date"], self.ref, self.hl)
        ht = train.dropna(subset=["hthg", "htag"])
        wh = R.decay_weights(ht["date"], self.ref, self.hl)
        self.p1 = float((wh * (ht.hthg + ht.htag)).sum() / (wh * (ht.hg + ht.ag)).sum())
        self.league_shots = float(np.average((train.hs + train.as_).dropna(), weights=w[train.hs.notna() & train.as_.notna()]))
        self.disp = {}
        for k, (yh, ya) in {"yellow": ("hy", "ay"), "corners": ("hc", "ac")}.items():
            d = train.dropna(subset=[yh, ya])
            wd = R.decay_weights(d["date"], self.ref, self.hl)
            fit = self.side[k]
            lam = np.array([self._side_lams(k, h, a)[0] + self._side_lams(k, h, a)[1] for h, a in zip(d.home, d.away)])
            self.disp[k] = float(max(1.0, (wd * (d[yh] + d[ya] - lam) ** 2).sum() / (wd * lam).sum()))
        self.priors = None
        if players is not None and len(players):
            self.priors = league_priors(players)
            self.players = players
        return self

    def _side_lams(self, key, home, away):
        return R.lambdas(self.side[key], home, away)

    def ratings_table(self):
        """Display-friendly team ratings (goal multiplier vs an average side)."""
        g = self.goal
        out = {}
        for t in g["a"]:
            out[t] = {
                "attack": float(np.exp(g["a"][t])), "defence": float(np.exp(-g["d"][t])),
                "n_eff": float(g["n_eff"].get(t, 0)), "newcomer": t in g["newcomers"],
                "yellow": float(np.exp(self.side["yellow"]["a"][t])), "corners": float(np.exp(self.side["corners"]["a"][t])),
                "shots": float(np.exp(self.side["shots"]["a"][t])),
            }
        return out

    # --------------------------------------------------------------- forecasting
    def squad(self, team):
        if self.priors is None:
            return squad_table(pd.DataFrame(), {p: {"g90": 0.1, "a90": 0.1, "y90": 0.15, "r90": 0.004, "price": 5.0}
                                                for p in ("GK", "DEF", "MID", "FWD")})
        return squad_table(self.players[self.players.club == team], self.priors)

    def forecast(self, home, away, seed=0, nsim=NSIM, squads=None):
        rng = np.random.default_rng(seed)
        g = self.goal
        lh, la = R.lambdas(g, home, away)
        grid = R.score_grid(lh, la, g["rho"])
        self.last_grid = grid
        ph, pdw, pa = R.outcome_probs(grid)
        pick = int(np.argmax([ph, pdw, pa]))
        ph_, pa_ = R.pick_scoreline(grid, pick)
        M = grid.shape[0]
        tot = np.add.outer(np.arange(M), np.arange(M))
        sq_h, sq_a = (squads or {}).get(home), (squads or {}).get(away)
        if sq_h is None:
            sq_h = self.squad(home)
        if sq_a is None:
            sq_a = self.squad(away)

        # ---------- side stats (expected values)
        ey = self._side_lams("yellow", home, away)
        er = self._side_lams("red", home, away)
        ec = self._side_lams("corners", home, away)
        es = self._side_lams("shots", home, away)
        et = self._side_lams("sot", home, away)
        ef = self._side_lams("fouls", home, away)
        p_pen = float(1 - np.exp(-PEN_BASE * (es[0] + es[1]) / self.league_shots))

        def over(mean, k, line):
            d = self.disp[k]
            if d <= 1.001:
                from scipy.stats import poisson
                return float(1 - poisson.cdf(int(line), mean))
            return float(1 - nbinom.cdf(int(line), mean / (d - 1), 1 / d))

        # ---------- Monte Carlo of the match
        flat = rng.choice(grid.size, nsim, p=grid.ravel())
        hg, ag = np.minimum(flat // M, MAXG_SIM), np.minimum(flat % M, MAXG_SIM)
        th, fh = sample_clock(rng, (nsim, MAXG_SIM), self.p1)
        ta, fa = sample_clock(rng, (nsim, MAXG_SIM), self.p1)
        mh = np.arange(MAXG_SIM)[None, :] < hg[:, None]
        ma = np.arange(MAXG_SIM)[None, :] < ag[:, None]
        INF = 1e9
        first_h = np.where(mh, th, INF).min(1)
        first_a = np.where(ma, ta, INF).min(1)
        any_goal = (hg + ag) > 0
        home_first = any_goal & (first_h <= first_a)
        away_first = any_goal & (first_a < first_h)
        first_time = np.where(home_first, first_h, np.where(away_first, first_a, np.nan))
        hth, hta = (mh & fh).sum(1), (ma & fa).sum(1)
        ht_res = np.where(hth > hta, "H", np.where(hth < hta, "A", "D"))
        ft_res = np.where(hg > ag, "H", np.where(hg < ag, "A", "D"))
        htft = {f"{a}/{b}": r4(np.mean((ht_res == a) & (ft_res == b))) for a in "HDA" for b in "HDA"}
        bh, ba = bin_of(th, fh), bin_of(ta, fa)
        goals_bin = {"home": [], "away": [], "any": []}
        for b in range(6):
            nh, na = (mh & (bh == b)).sum(1), (ma & (ba == b)).sum(1)
            goals_bin["home"].append(r4(nh.mean()))
            goals_bin["away"].append(r4(na.mean()))
            goals_bin["any"].append(r4(((nh + na) > 0).mean()))
        ft = first_time[~np.isnan(first_time)]

        def scorer_probs(sq, mask, times, idx_draw, is_first_side):
            cdf = np.cumsum(sq.p_goal.to_numpy())
            cdf[-1] = 1.0
            idx = np.minimum(np.searchsorted(cdf, rng.random((nsim, MAXG_SIM))), len(sq) - 1)
            anytime = np.array([((idx == k) & mask).any(1).mean() for k in range(len(sq))])
            jfirst = np.where(mask, times, INF).argmin(1)
            fs = idx[np.arange(nsim), jfirst]
            first = np.array([np.mean(is_first_side & (fs == k)) for k in range(len(sq))])
            return anytime, first

        any_h, fst_h = scorer_probs(sq_h, mh, th, None, home_first)
        any_a, fst_a = scorer_probs(sq_a, ma, ta, None, away_first)

        # cards
        yh = np.minimum(rng.poisson(ey[0], nsim), 8)
        ya = np.minimum(rng.poisson(ey[1], nsim), 8)

        def booked(sq, n):
            w = sq.w_yellow.to_numpy()
            cdf = np.cumsum(w)
            cdf[-1] = 1.0
            idx = np.minimum(np.searchsorted(cdf, rng.random((nsim, 8))), len(sq) - 1)
            mask = np.arange(8)[None, :] < n[:, None]
            return np.array([((idx == k) & mask).any(1).mean() for k in range(len(sq))])

        bk_h, bk_a = booked(sq_h, yh), booked(sq_a, ya)
        rh, ra = rng.poisson(er[0], nsim), rng.poisson(er[1], nsim)

        # ---------- headline probabilities
        scorers = {}
        for side, sq, anyp, fstp in (("home", sq_h, any_h, fst_h), ("away", sq_a, any_a, fst_a)):
            order = np.argsort(-anyp)[:6]
            scorers[side] = [{"name": sq.name.iloc[k], "pos": sq.pos.iloc[k], "p_anytime": r4(anyp[k]),
                              "p_first": r4(fstp[k]), "share": r4(sq.p_goal.iloc[k])} for k in order]
        both = sorted([(s["p_first"], s["name"], side) for side in ("home", "away") for s in scorers[side]], reverse=True)
        cards = {}
        for side, sq, bk in (("home", sq_h, bk_h), ("away", sq_a, bk_a)):
            order = np.argsort(-bk)[:5]
            cards[side] = [{"name": sq.name.iloc[k], "pos": sq.pos.iloc[k], "p_booked": r4(bk[k])} for k in order]

        # ---------- the "most likely story" for the predicted scoreline
        timeline = self._scenario(ph_, pa_, sq_h, sq_a, ey, er, any_h, any_a, bk_h, bk_a, p_pen)

        top = np.dstack(np.unravel_index(np.argsort(-grid.ravel())[:8], grid.shape))[0]
        detail = {
            "grid": [[r4(x) for x in row[:7]] for row in grid[:7]],
            "top_scores": [[int(i), int(j), r4(grid[i, j])] for i, j in top],
            "timeline": timeline,
            "goal_bins": {"labels": BIN_LABELS, **goals_bin},
            "first_goal": {"home": r4(home_first.mean()), "away": r4(away_first.mean()), "none": r4(1 - any_goal.mean()),
                           "median_minute": float(np.round(np.median(ft), 0)) if len(ft) else None,
                           "p_before_30": r4(np.mean(ft <= 30)) if len(ft) else 0.0},
            "ht": {"H": r4(np.mean(ht_res == "H")), "D": r4(np.mean(ht_res == "D")), "A": r4(np.mean(ht_res == "A"))},
            "htft": htft,
            "p_goal_before_halftime": r4(((hth + hta) > 0).mean()),
            "p_late_goal": goals_bin["any"][5],
            "scorers": scorers,
            "first_scorer": [{"name": n, "side": s, "p": r4(p)} for p, n, s in both[:5]],
            "cards": {**cards, "p_red_home": r4((rh > 0).mean()), "p_red_away": r4((ra > 0).mean()),
                      "p_over_3_5_yellows": r4(over(ey[0] + ey[1], "yellow", 3)),
                      "p_over_4_5_yellows": r4(over(ey[0] + ey[1], "yellow", 4))},
            "stats": {
                "corners": {"home": r4(ec[0]), "away": r4(ec[1]), "p_over_9_5": r4(over(ec[0] + ec[1], "corners", 9)),
                            "p_over_10_5": r4(over(ec[0] + ec[1], "corners", 10))},
                "shots": {"home": r4(es[0]), "away": r4(es[1])}, "sot": {"home": r4(et[0]), "away": r4(et[1])},
                "fouls": {"home": r4(ef[0]), "away": r4(ef[1])},
            },
            "markets": {
                "over15": r4(grid[tot > 1.5].sum()), "over25": r4(grid[tot > 2.5].sum()), "over35": r4(grid[tot > 3.5].sum()),
                "btts": r4(grid[1:, 1:].sum()), "cs_home": r4(grid[:, 0].sum()), "cs_away": r4(grid[0, :].sum()),
                "home_win_by2": r4(sum(grid[i, j] for i in range(M) for j in range(M) if i - j >= 2)),
                "away_win_by2": r4(sum(grid[i, j] for i in range(M) for j in range(M) if j - i >= 2)),
                "nil_nil": r4(grid[0, 0]), "p_penalty": r4(p_pen),
            },
            "xg": {"home": r4(lh), "away": r4(la)},
        }
        fields = {
            "xg_home": r4(lh), "xg_away": r4(la), "p_home": r4(ph), "p_draw": r4(pdw), "p_away": r4(pa),
            "pred_home": ph_, "pred_away": pa_, "p_over25": detail["markets"]["over25"], "p_btts": detail["markets"]["btts"],
            "p_home_cs": detail["markets"]["cs_home"], "p_away_cs": detail["markets"]["cs_away"],
            "exp_yellow_home": r4(ey[0]), "exp_yellow_away": r4(ey[1]),
            "p_red": r4(((rh + ra) > 0).mean()), "exp_corners": r4(ec[0] + ec[1]), "p_penalty": r4(p_pen),
        }
        return fields, detail

    # ----------------------------------------------------------------- scenario
    def _scenario(self, hk, ak, sq_h, sq_a, ey, er, any_h, any_a, bk_h, bk_a, p_pen):
        ev = []

        def goals(k, sq, side):
            if k == 0:
                return
            w = sq.p_goal.to_numpy().copy()
            a = sq.p_assist.to_numpy().copy()
            for j in range(1, k + 1):
                q = float(beta_dist.ppf(0.5, j, k - j + 1))
                m, first = quantile_clock(q, self.p1)
                s = int(np.argmax(w))
                w[s] *= 0.3
                a2 = a.copy()
                a2[s] = 0
                asst = int(np.argmax(a2))
                a[asst] *= 0.5
                ev.append({"m": m, "first": bool(first), "type": "goal", "side": side, "player": sq.name.iloc[s],
                           "assist": sq.name.iloc[asst], "p": r4(any_h[s] if side == "home" else any_a[s])})

        goals(hk, sq_h, "home")
        goals(ak, sq_a, "away")
        for side, sq, exp, bk in (("home", sq_h, ey[0], bk_h), ("away", sq_a, ey[1], bk_a)):
            n = int(round(exp))
            order = list(np.argsort(-bk))
            for j in range(n):
                q = (j + 0.5) / max(n, 1)
                u = -0.4 + np.sqrt(0.16 + 1.8 * q)
                m = 90 * u
                ev.append({"m": float(m), "first": bool(m <= 45), "type": "yellow", "side": side,
                           "player": sq.name.iloc[order[j]], "p": r4(bk[order[j]])})
        ev.sort(key=lambda e: (e["m"], e["side"] == "away"))
        # nudge simultaneous events apart
        last = -9
        for e in ev:
            if e["m"] - last < 1:
                e["m"] = last + 1
            last = e["m"]
        out = []
        for e in ev:
            minute = max(1, int(round(e["m"])))
            first = e["first"] if e["type"] == "goal" else minute <= 45
            lab = label_minute(e["m"], e["first"]) if e["type"] == "goal" else f"{minute}'"
            out.append({**{k: v for k, v in e.items() if k not in ("m", "first")}, "minute": minute, "label": lab,
                        "pos": r4(min(e["m"], 96) / 96 * 100)})
        return out
