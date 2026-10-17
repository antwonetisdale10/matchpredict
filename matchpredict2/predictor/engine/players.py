"""Turn a squad list into per-player probabilities for: scoring, assisting, being booked, sent off.

Everything is built from LAST season's numbers (goals, assists, minutes, cards) + the player's
starting price as a quality prior. Newcomers with no history fall back to position priors
scaled by price and their rank in the squad. Nothing from the season being predicted is used.
"""
import numpy as np
import pandas as pd

POS_FALLBACK = {"GK": 0.002, "DEF": 0.035, "MID": 0.10, "FWD": 0.26}
K90 = 8.0  # pseudo-90s of prior used for shrinkage


def league_priors(all_players):
    d = all_players[all_players.min_prev.fillna(0) > 0]
    out = {}
    for pos, g in d.groupby("pos"):
        n90 = g.min_prev.sum() / 90
        out[pos] = {
            "g90": max(g.g_prev.sum() / n90, 0.001), "a90": max(g.a_prev.sum() / n90, 0.001),
            "y90": max(g.y_prev.sum() / n90, 0.01), "r90": max(g.r_prev.sum() / n90, 0.0005),
            "price": g.start_price.mean(),
        }
    for pos, v in POS_FALLBACK.items():  # positions missing from the data
        out.setdefault(pos, {"g90": v, "a90": v, "y90": 0.15, "r90": 0.004, "price": 5.0})
    return out


def squad_table(players, priors):
    """players: DataFrame of one team's squad -> DataFrame with probabilities (sum to 1 per column)."""
    p = players.copy().reset_index(drop=True)
    if p.empty:
        rows = [("Forward", "FWD", .55), ("Midfielder", "MID", .33), ("Defender", "DEF", .11), ("Goalkeeper", "GK", .01)]
        p = pd.DataFrame([{"name": n, "pos": pos, "start_price": None, "g_prev": None, "a_prev": None,
                           "min_prev": None, "pen_order": None, "fk_order": None, "y_prev": None, "r_prev": None}
                          for n, pos, _ in rows])
    price_rank = p.start_price.fillna(0).rank(ascending=False, method="first")
    share, g_rate, a_rate, y_rate, r_rate = [], [], [], [], []
    gks = p[p.pos == "GK"]
    main_gk = (gks.assign(k=gks.min_prev.fillna(0) + gks.start_price.fillna(0)).k.idxmax() if len(gks) else -1)
    for i, r in p.iterrows():
        pr = priors[r.pos]
        pf = 1.0 if pd.isna(r.start_price) or not pr["price"] else np.clip((r.start_price / pr["price"]) ** 1.5, 0.35, 3.0)
        has = not pd.isna(r.min_prev) and r.min_prev > 0
        n90 = (r.min_prev / 90) if has else 0.0
        gp, ap = pr["g90"] * pf, pr["a90"] * pf
        g_rate.append(((r.g_prev if has else 0) + gp * K90) / (n90 + K90))
        a_rate.append(((r.a_prev if has else 0) + ap * K90) / (n90 + K90))
        y_rate.append(((r.y_prev if has else 0) + pr["y90"] * K90) / (n90 + K90))
        r_rate.append(((r.r_prev if has else 0) + pr["r90"] * K90 * 3) / (n90 + K90 * 3))
        # share of the 90 minutes the player is expected to be on the pitch
        if has and r.min_prev >= 600:
            s = min(1.0, r.min_prev / 2700)
        else:  # no / thin history: by squad price rank (regular XI ~ top 12)
            rk = price_rank[i]
            s = 0.75 if rk <= 12 else 0.3 if rk <= 18 else 0.08
            if has:
                s = min(s, 0.4)
        if r.pos == "GK":
            s = 0.9 if i == main_gk else 0.01
        share.append(s)
    p["share"] = share
    pen = np.where(p.pen_order.fillna(9) == 1, 1.35, 1.0) * np.where(p.fk_order.fillna(9) == 1, 1.05, 1.0)
    w_goal = np.array(g_rate) * p.share * pen
    w_ast = np.array(a_rate) * p.share
    w_yel = np.array(y_rate) * p.share
    w_red = np.array(r_rate) * p.share
    for col, w in (("p_goal", w_goal), ("p_assist", w_ast), ("w_yellow", w_yel), ("w_red", w_red)):
        s = float(np.sum(w))
        p[col] = w / s if s > 0 else 1 / len(p)
    return p[["name", "pos", "share", "p_goal", "p_assist", "w_yellow", "w_red"]]
