# Matchday — Premier League 2026/27 match predictor (Django)

Predicts all 380 fixtures of 2026/27 from 20 seasons of results (2005/06–2024/25), with a website to browse,
filter by team, make your own predictions, and earn points/levels/badges.

## Run it
```bash
pip install -r requirements.txt   # works on Python 3.8+ (Django 4.2+)
python manage.py runserver          # db.sqlite3 is prebuilt, open http://localhost:8000
```
Rebuild from scratch: `python manage.py migrate && python manage.py load_data && python manage.py predict && python manage.py backtest`
Tests (incl. the no-peeking test): `python manage.py test predictor`

## The hard rule: no 2026/27 results are ever used
`predictor/engine/data.py::training_frame` only returns seasons that *start before* the one being predicted.
`test_target_season_results_never_change_the_forecast` fills 2026-style results with random numbers and checks the
forecast is byte-identical. The fixture file was converted with `tools/openfootball_to_fixtures.py`, which discards scores.
Player squads come from the 26/27 roster for names/clubs/start price only; all numbers come from the finished 2025/26 season.

## What gets predicted (per match)
Score + win/draw/loss, scoreline map, goal markets (O/U, BTTS, clean sheets), a minute-by-minute "most likely story"
(goals with scorer + assist, yellow cards), when goals are likely (15-min windows), first goal side/time, half-time odds,
first scorer, anytime scorers, who gets booked, red-card and penalty chance, corners/shots/fouls. Plus a simulated
season table (title/top-4/relegation odds), Golden Boot race, and team ratings.

## Method & honesty notes
- Fitted: time-decayed Dixon–Coles Poisson ratings (half-life 1.5y, ridge 8; tuned by walk-forward backtest), HT/FT goal split,
  cards/corners/shots/fouls on the same footing. Backtest (10 seasons, last season hidden): ~52% results right vs ~44% baseline,
  log-loss 0.99 vs 1.07.
- **Assumed (CSV has no minutes/scorers/penalties):** in-half goal-timing curve, stoppage time, penalty rate (0.28/game), player
  scoring/booking shares from 2025/26 numbers. See the Accuracy page.
- Trained on 2005/06–2025/26. Squads reflect the summer 2026 window including deadline day (1 Sept 2026), but only changes I could confirm from several sources; rebuild with `tools/apply_transfers.py`. Injuries, suspensions and managers are not modelled. Coventry and Hull have no recent Premier League data and use the newly-promoted prior.
  Leeds/Sunderland/Coventry/Hull use a newly-promoted prior.
- The fixture source listed scores for dates that haven't happened yet, so I treated it as fixtures only.

## Features
Matchweek browser + team filter · match detail · projected table · teams/players · predictions via bottom sheet ·
points (result 3, exact +5, HT +2, cards/corners, first scorer/window, upset +3, streaks), 6 levels, 9 badges, leaderboard vs the model ·
**Data Studio** (`/studio/`): add seasons, upload or generate fixtures, upload squads, run a forecast for any future season,
record results (CSV or per match) to score picks. Set `DATA_STUDIO_STAFF_ONLY=1` before hosting publicly.
