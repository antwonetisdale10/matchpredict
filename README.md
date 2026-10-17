# Matchday: Premier League match predictor

A  website that predicts all 380 Premier League fixtures for 2026/27 from 20 seasons of results
(2005/06 to 2024/25). You can browse every matchweek, filter to one team, make your own predictions, and earn
points, levels and badges when real results come in.

## Quick start

Requires Python 3.8+ (works with Django 4.2 and newer).

```bash
pip install -r requirements.txt
python manage.py runserver
```

Then open http://localhost:8000. The database (`db.sqlite3`) comes prebuilt with the predictions, so there is
nothing else to set up.

To rebuild everything from scratch:

```bash
python manage.py migrate
python manage.py load_data     # 20 seasons + 2026/27 fixtures (no results) + squads
python manage.py predict       # forecast every 2026/27 fixture (about a minute)
python manage.py backtest      # accuracy on past seasons, used by the Accuracy page
```

Run the tests with `python manage.py test predictor`.

## The hard rule: 2026/27 results are never used

The model only ever trains on seasons that start before the season it is predicting.

- `predictor/engine/data.py` (`training_frame`) is the only place the engine reads match history, and it
  filters to earlier seasons.
- A test fills 2026/27-style results with random numbers and checks the forecast does not change at all
  (`test_target_season_results_never_change_the_forecast`).
- The fixture list was converted with `tools/openfootball_to_fixtures.py`, which throws away any scores.
- Squads use the 2026/27 roster for names, clubs and starting price only. All player numbers (goals, assists,
  minutes, cards) come from the finished 2025/26 season.

## What gets predicted for each match

- Predicted score, win/draw/loss probabilities, and a scoreline probability map
- Goal markets: over/under 1.5, 2.5 and 3.5, both teams to score, clean sheets, win by 2+
- A minute-by-minute "most likely story": goals with scorer and assist, plus yellow cards
- When goals are likely (15-minute windows), first goal side and typical minute, half-time odds
- First scorer and anytime scorer odds, and who is most likely to be booked
- Red-card chance, penalty chance, expected corners, shots, shots on target and fouls

Season-level extras: a simulated league table (title, top-4 and relegation odds), a Golden Boot race,
a card watch, and team attack/defence ratings.

## Pages

| Page | What it does |
| --- | --- |
| `/` | Current matchweek, model highlights, projected finish, leaderboard preview |
| `/matches/` | Browse by matchweek, or filter to one team's full season |
| `/match/<id>/` | Full prediction for one match, plus head to head and your pick |
| `/table/` | Projected final table with finishing-position odds |
| `/teams/`, `/team/<name>/` | Ratings, fixtures with difficulty, likely scorers |
| `/players/` | Golden Boot race and card watch |
| `/leaderboard/` | Points, levels, and how users compare with the model |
| `/me/` | Your points, level, badges and picks |
| `/accuracy/` | Backtest results, calibration, and what is fitted vs assumed |
| `/studio/` | Data Studio: add data, predict a new season, record results |

## Make your own predictions

Create an account, open any match that has not kicked off, and tap Predict. Picks lock at kick-off.

| Pick | Points |
| --- | --- |
| Correct result | +3 |
| Exact score | +5 |
| Half-time result | +2 |
| Red-card call (yes / no) | +4 / +1 |
| Yellow cards (3.5 line) or corners (9.5 line) | +1 each |
| First-goal window | +2 |
| First scorer | +4 |
| Upset call (model gave it under 25%) | +3 |
| Streak of 3+ correct results | +1 to +3 |

Bonus picks are optional and never cost you points. First-scorer and first-goal bonuses are only scored when
goal events are recorded for the match. There are 6 levels (Rookie, Fan, Analyst, Pundit, Mastermind, Oracle),
9 badges, and a leaderboard that includes the model as a benchmark.

## Data Studio: add data and predict a future season

Open `/studio/` (sign in first). Sample CSVs are linked from the page.

1. **Add a finished season.** Upload a football-data.co.uk style CSV (Date, HomeTeam, AwayTeam, FTHG, FTAG,
   and optionally half-time, cards, corners, shots, fouls). Add 2025/26 here to improve the 2026/27 forecast.
2. **Load fixtures.** Upload a CSV with `matchweek,date,time,home,away`, or paste the 20 clubs and let the
   studio generate a balanced 38-week home-and-away schedule. Score columns are ignored.
3. **Add squads (optional).** Used for scorer and card predictions. Without them, scorers fall back to
   generic position groups.
4. **Run the forecast.** Pick the season and click Predict. It uses only results from earlier seasons.
5. **Record results.** Upload a results CSV or enter matches one at a time. This scores everyone's picks.
   The model never reads these results for the season it already predicted.

Command-line equivalents: `python manage.py load_data --target 2027` and `python manage.py predict --season 2027`.

## How the model works

Fitted from the data:

- Team attack and defence strength plus home advantage, using a time-weighted Poisson model with the
  Dixon-Coles low-score correction. Recent seasons count more (half-life 1.5 years), and ratings are
  shrunk toward average when a team has little recent data.
- The first-half vs second-half goal split (about 44% before the break).
- Each team's yellow cards, reds, corners, shots, shots on target and fouls, modelled the same way.
- Match outcomes, goal timing and cards come from a 20,000-run Monte Carlo simulation per match, and the
  whole season is simulated 4,000 times for the table.

Assumed, because the data cannot tell us:

- The match CSVs have no goal minutes, so timing within each half uses a standard rising-intensity curve and
  typical stoppage time.
- The penalty rate is a flat prior of about 0.28 per game, scaled by shot volume.
- Scorers and booked players come from last season's player numbers, not from this season's matches.
- Referees are unknown this far ahead, so card predictions are team-level.

Newly promoted teams, and teams with no recent top-flight data, use a "newly promoted" prior estimated from
past promoted sides.

## Data used

### Match history (the model's training data)
- **What:** the 20 season files in `data/raw/` (`season-0506.csv` to `season-2425.csv`), covering 2005/06 to 2024/25.
  That is 7,600 matches, 380 per season, in football-data.co.uk format.
- **Columns used:** date, home and away team, full-time goals (`FTHG`, `FTAG`), half-time goals (`HTHG`, `HTAG`),
  shots (`HS`, `AS`), shots on target (`HST`, `AST`), fouls (`HF`, `AF`), corners (`HC`, `AC`),
  yellow cards (`HY`, `AY`) and red cards (`HR`, `AR`).
- **Not used:** the referee column. The referee for a future match is unknown, so card predictions are team-level.
- **Not included:** goal minutes, scorers, penalties, injuries and lineups. The files don't have them.

### 2026/27 fixtures
- **What:** `data/fixtures_2627.csv`, 380 matches with matchweek, date, kick-off time, home and away team.
- **Source:** the openfootball England fixture list on GitHub, converted by `tools/openfootball_to_fixtures.py`.
- **Scores dropped:** the source also listed scores, including for dates that haven't happened yet.
  The converter discards every score, so the file holds fixtures only.
- **Teams:** the 20 clubs are the 17 who stayed up plus Coventry, Hull and Ipswich, who were promoted.
  West Ham, Burnley and Wolves were relegated. Source for the club list: Wikipedia's 2026-27 Premier League page.

### Player data (for scorers and bookings)
- **What:** `data/players/players_2627.csv`, 571 players. 436 of them have 2025/26 history.
- **Source:** the Fantasy Premier League data on GitHub (vaastav/Fantasy-Premier-League), built by `tools/build_players.py`.
- **From the 2026/27 roster, identity only:** name, club, position and starting price.
- **From the finished 2025/26 season, performance only:** goals, assists, minutes, penalty and free-kick duties,
  yellow cards and red cards.
- **Newcomers with no 2025/26 history:** their rates come from position averages, scaled by starting price and
  their rank in the squad.
- **Never read:** any 2026/27 goals, assists, minutes or cards.

### Assumptions (no data behind them)
- Goal timing inside each half, stoppage time, the penalty rate (about 0.28 per game), and how card timing
  builds through a match are fixed priors, not fitted. They are listed on the Accuracy page.

### Data you add
- Anything uploaded in Data Studio is stored in the database. A finished season becomes training data for later
  seasons only. Results recorded for a season are used to score picks and never to forecast that same season.

## Accuracy

Backtested over 10 seasons, with the most recent season hidden each time to mimic the missing 2025/26 data:
about 52% of results right against about 44% for a baseline that always uses the historical home/draw/away
rates, and log loss of about 0.99 against 1.07. Exact scores land roughly one match in ten. Football is hard
to predict, so treat the output as probabilities, not certainties. See `/accuracy/` for per-season numbers
and a calibration chart.

## Known limits

- The model does not know the 2025/26 season, transfers, injuries or managers. Upload 2025/26 in Data Studio
  to improve it.
- The fixture source listed scores for dates that had not happened yet, so only the fixtures were used.
- No club badges are used. Team colours are decorative.
- Data Studio is open to any signed-in user by default, for local use. Before hosting this publicly, set
  `DATA_STUDIO_STAFF_ONLY=1` and turn off debug mode with `DJANGO_DEBUG=0`.
- This is a statistical forecast, not betting advice.

## Project layout

```
config/                  Django settings and URLs
predictor/models.py      Season, Team, Match, Prediction, UserPrediction, badges, ratings
predictor/engine/        ratings.py (model), simulate.py (match simulator), players.py,
                         pipeline.py (run a forecast), backtest.py, data.py (leakage guard)
predictor/rewards.py     Scoring, levels, badges, streaks
predictor/importers.py   CSV importers and fixture generator
predictor/views.py       Pages and the Data Studio
predictor/templates/     HTML (Apple-style design)
predictor/static/        app.css and app.js (springs, bottom sheet, segmented controls)
data/raw/                The 20 season CSVs
data/fixtures_2627.csv   2026/27 fixtures (no scores)
data/players/            2026/27 squads with 2025/26 numbers
tools/                   Fixture converter, squad builder, tuning script
```

