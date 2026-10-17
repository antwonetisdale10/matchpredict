"""Training data access - the ONLY door through which the engine reads match history.

Leakage guard: rows come exclusively from seasons that START BEFORE the season being
predicted (minus an optional `gap` to mimic missing recent seasons in a backtest). Results
that exist in the DB for the target season (e.g. entered later to settle user picks) are
therefore invisible to the model by construction.
"""
import pandas as pd

from predictor.models import Match

FIELDS = {
    "kickoff": "date", "season__start_year": "season", "home__name": "home", "away__name": "away",
    "home_goals": "hg", "away_goals": "ag", "ht_home": "hthg", "ht_away": "htag",
    "home_shots": "hs", "away_shots": "as_", "home_sot": "hst", "away_sot": "ast",
    "home_fouls": "hf", "away_fouls": "af", "home_corners": "hc", "away_corners": "ac",
    "home_yellow": "hy", "away_yellow": "ay", "home_red": "hr", "away_red": "ar",
}


def training_frame(target_year, gap=0):
    cutoff = target_year - gap
    rows = Match.objects.filter(season__start_year__lt=cutoff, home_goals__isnull=False).values(*FIELDS)
    df = pd.DataFrame(list(rows)).rename(columns=FIELDS)
    if df.empty:
        raise ValueError("no completed seasons before the target season - import historical data first")
    assert (df["season"] < cutoff).all(), "leakage guard tripped: training data includes target season"
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    return df.sort_values("date").reset_index(drop=True)


def season_frame(season_year):
    """Matches of one season WITH results - used only by the backtest to score itself."""
    rows = Match.objects.filter(season__start_year=season_year, home_goals__isnull=False).values(*FIELDS)
    df = pd.DataFrame(list(rows)).rename(columns=FIELDS)
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    return df.sort_values("date").reset_index(drop=True)
