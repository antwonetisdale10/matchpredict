"""CSV importers (used by management commands AND the Data Studio upload page)."""
import csv
import io
import re
from datetime import date, datetime, time, timedelta

import pandas as pd
from django.db import transaction
from django.utils import timezone

from .models import Match, Player, Season, Team

SHORTS = {
    "Arsenal": "ARS", "Aston Villa": "AVL", "Bournemouth": "BOU", "Brentford": "BRE", "Brighton": "BHA",
    "Burnley": "BUR", "Chelsea": "CHE", "Coventry": "COV", "Crystal Palace": "CRY", "Everton": "EVE",
    "Fulham": "FUL", "Hull": "HUL", "Ipswich": "IPS", "Leeds": "LEE", "Leicester": "LEI",
    "Liverpool": "LIV", "Man City": "MCI", "Man United": "MUN", "Newcastle": "NEW",
    "Nott'm Forest": "NFO", "Sheffield United": "SHU", "Southampton": "SOU", "Sunderland": "SUN",
    "Tottenham": "TOT", "West Ham": "WHU", "Wolves": "WOL", "West Brom": "WBA", "Watford": "WAT",
    "Norwich": "NOR", "Stoke": "STK", "Swansea": "SWA", "Cardiff": "CAR", "Middlesbrough": "MID",
    "QPR": "QPR", "Reading": "REA", "Blackburn": "BLB", "Bolton": "BOL", "Wigan": "WIG",
    "Birmingham": "BIR", "Portsmouth": "POR", "Derby": "DER", "Charlton": "CHA", "Sheffield Weds": "SHW",
    "Blackpool": "BLP", "Huddersfield": "HUD", "Luton": "LUT",
}
# Alternative spellings people use -> football-data spelling
ALIASES = {
    "Manchester City": "Man City", "Manchester United": "Man United", "Manchester Utd": "Man United",
    "Man Utd": "Man United", "Tottenham Hotspur": "Tottenham", "Spurs": "Tottenham",
    "Newcastle United": "Newcastle", "Nottingham Forest": "Nott'm Forest", "Nottm Forest": "Nott'm Forest",
    "Brighton & Hove Albion": "Brighton", "Brighton and Hove Albion": "Brighton",
    "Wolverhampton Wanderers": "Wolves", "Wolverhampton": "Wolves", "West Ham United": "West Ham",
    "Leeds United": "Leeds", "Leicester City": "Leicester", "Hull City": "Hull", "Ipswich Town": "Ipswich",
    "Coventry City": "Coventry", "Norwich City": "Norwich", "Stoke City": "Stoke", "Swansea City": "Swansea",
    "Cardiff City": "Cardiff", "AFC Bournemouth": "Bournemouth", "Sheffield Utd": "Sheffield United",
    "Sunderland AFC": "Sunderland", "Luton Town": "Luton", "Huddersfield Town": "Huddersfield",
}
LONDON = timezone.get_default_timezone()


def norm_team(name):
    name = re.sub(r"\s+", " ", str(name).strip())
    name = ALIASES.get(name, name)
    # drop "FC"/"AFC" suffixes from openfootball-style names
    name = re.sub(r"\s+(A?FC)$", "", name)
    return ALIASES.get(name, name)


def get_team(name):
    name = norm_team(name)
    team, _ = Team.objects.get_or_create(name=name, defaults={"short": SHORTS.get(name, name[:3].upper())})
    return team


def get_season(start_year):
    return Season.objects.get_or_create(start_year=int(start_year))[0]


def _read(fileobj):
    if hasattr(fileobj, "read"):
        raw = fileobj.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8-sig", errors="replace")
        return pd.read_csv(io.StringIO(raw))
    return pd.read_csv(fileobj, encoding="utf-8-sig")


def _parse_date(v):
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unrecognised date: {s!r}")


def _aware(d, hhmm="15:00"):
    h, m = (int(x) for x in str(hhmm).split(":")[:2])
    return timezone.make_aware(datetime.combine(d, time(h, m)), LONDON)


def _int(v):
    return None if pd.isna(v) else int(v)


RESULT_COLS = {  # csv col -> Match field
    "FTHG": "home_goals", "FTAG": "away_goals", "HTHG": "ht_home", "HTAG": "ht_away",
    "HS": "home_shots", "AS": "away_shots", "HST": "home_sot", "AST": "away_sot",
    "HF": "home_fouls", "AF": "away_fouls", "HC": "home_corners", "AC": "away_corners",
    "HY": "home_yellow", "AY": "away_yellow", "HR": "home_red", "AR": "away_red",
}


def _assign_matchweeks(rows):
    """football-data files have no matchweek column: rebuild it (each team plays once per week)."""
    played_in_week, week, weeks = set(), 1, []
    for r in rows:
        if r["home"] in played_in_week or r["away"] in played_in_week:
            week += 1
            played_in_week = set()
        played_in_week.update((r["home"], r["away"]))
        weeks.append(week)
    return weeks


@transaction.atomic
def import_season_csv(fileobj, start_year, replace=False):
    """Import a COMPLETED season in football-data.co.uk format (with results)."""
    df = _read(fileobj)
    df.columns = [c.strip() for c in df.columns]
    need = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"}
    if need - set(df.columns):
        raise ValueError(f"missing columns: {', '.join(sorted(need - set(df.columns)))}")
    df = df.dropna(subset=["HomeTeam", "AwayTeam", "FTHG", "FTAG"]).copy()
    df["_d"] = df["Date"].map(_parse_date)
    df = df.sort_values("_d", kind="stable")
    season = get_season(start_year)
    if season.matches.exists():
        if not replace:
            return {"season": season.label, "created": 0, "skipped": True}
        season.matches.all().delete()
    rows = [{"home": norm_team(a), "away": norm_team(b)} for a, b in zip(df["HomeTeam"], df["AwayTeam"])]
    weeks = _assign_matchweeks(rows)
    objs = []
    for (_, r), wk in zip(df.iterrows(), weeks):
        fields = {f: _int(r[c]) for c, f in RESULT_COLS.items() if c in df.columns}
        ref = str(r["Referee"]).strip() if "Referee" in df.columns and not pd.isna(r["Referee"]) else ""
        objs.append(Match(season=season, matchweek=wk, kickoff=_aware(r["_d"]), home=get_team(r["HomeTeam"]),
                          away=get_team(r["AwayTeam"]), referee=ref, **fields))
    Match.objects.bulk_create(objs, ignore_conflicts=True)
    season.is_complete = True
    season.save()
    return {"season": season.label, "created": len(objs), "skipped": False}


@transaction.atomic
def import_fixtures_csv(fileobj, start_year, replace=False):
    """Fixture list with NO results. Columns: matchweek,date,time,home,away.
    Any score columns in the file are ignored on purpose."""
    df = _read(fileobj)
    df.columns = [c.strip().lower() for c in df.columns]
    aliases = {"hometeam": "home", "awayteam": "away", "week": "matchweek", "gw": "matchweek",
               "kickoff": "time", "kick_off": "time"}
    df = df.rename(columns=aliases)
    need = {"matchweek", "date", "home", "away"}
    if need - set(df.columns):
        raise ValueError(f"fixtures csv needs columns: {', '.join(sorted(need))} (+ optional time)")
    season = get_season(start_year)
    if season.matches.exists():
        if not replace:
            raise ValueError(f"{season.label} already has fixtures - tick 'replace' to overwrite")
        season.matches.all().delete()
    objs = []
    for _, r in df.iterrows():
        t = r["time"] if "time" in df.columns and not pd.isna(r["time"]) else "15:00"
        objs.append(Match(season=season, matchweek=int(r["matchweek"]), kickoff=_aware(_parse_date(r["date"]), t),
                          home=get_team(r["home"]), away=get_team(r["away"])))
    Match.objects.bulk_create(objs)
    return {"season": season.label, "created": len(objs)}


def generate_fixtures(team_names, start_year, first_saturday=None):
    """Double round-robin via the circle method -> list of dict(matchweek,date,time,home,away).
    Used when a future season has no official fixture list yet."""
    teams = [norm_team(t) for t in team_names]
    if len(teams) % 2:
        teams.append(None)
    n = len(teams)
    if first_saturday is None:
        d = date(int(start_year), 8, 15)
        first_saturday = d + timedelta(days=(5 - d.weekday()) % 7)
    rounds, arr = [], teams[:]
    for r in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = arr[i], arr[n - 1 - i]
            if a is None or b is None:
                continue
            pairs.append((a, b) if (r + i) % 2 == 0 else (b, a))
        rounds.append(pairs)
        arr = [arr[0]] + [arr[-1]] + arr[1:-1]
    second = [[(b, a) for a, b in rd] for rd in rounds]
    out, wk = [], 1
    for rd in rounds + second:
        day = first_saturday + timedelta(days=7 * (wk - 1))
        for k, (h, a) in enumerate(rd):
            out.append({"matchweek": wk, "date": day.isoformat(), "time": ("12:30", "15:00", "17:30")[k % 3],
                        "home": h, "away": a})
        wk += 1
    return out


@transaction.atomic
def import_generated_fixtures(team_names, start_year, replace=False):
    rows = generate_fixtures(team_names, start_year)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["matchweek", "date", "time", "home", "away"])
    w.writeheader()
    w.writerows(rows)
    buf.seek(0)
    return import_fixtures_csv(buf, start_year, replace=replace)


@transaction.atomic
def import_results_csv(fileobj, start_year):
    """Record real results for fixtures that already exist (football-data format). These are
    only used to settle user predictions - the model never sees them."""
    df = _read(fileobj)
    df.columns = [c.strip() for c in df.columns]
    season = get_season(start_year)
    updated, missing = 0, []
    for _, r in df.dropna(subset=["HomeTeam", "AwayTeam", "FTHG", "FTAG"]).iterrows():
        h, a = norm_team(r["HomeTeam"]), norm_team(r["AwayTeam"])
        m = season.matches.filter(home__name=h, away__name=a).first()
        if not m:
            missing.append(f"{h} v {a}")
            continue
        for c, f in RESULT_COLS.items():
            if c in df.columns:
                setattr(m, f, _int(r[c]))
        m.save()
        updated += 1
    return {"season": season.label, "updated": updated, "missing": missing}


@transaction.atomic
def import_players_csv(fileobj, start_year, replace=True):
    df = _read(fileobj)
    season = get_season(start_year)
    if replace:
        season.players.all().delete()
    objs = []
    f = lambda v: None if pd.isna(v) else float(v)  # noqa: E731
    for _, r in df.iterrows():
        objs.append(Player(
            season=season, team=get_team(r["club"]), name=str(r["name"]),
            full_name=f"{r.get('first_name', '')} {r.get('second_name', '')}".strip() if "first_name" in df else "",
            pos=str(r["pos"]), start_price=f(r.get("start_price")), goals_prev=f(r.get("g_prev")),
            assists_prev=f(r.get("a_prev")), minutes_prev=f(r.get("min_prev")), pen_order=f(r.get("pen_order")),
            fk_order=f(r.get("fk_order")), yellows_prev=f(r.get("y_prev")), reds_prev=f(r.get("r_prev"))))
    Player.objects.bulk_create(objs)
    return {"season": season.label, "players": len(objs)}
