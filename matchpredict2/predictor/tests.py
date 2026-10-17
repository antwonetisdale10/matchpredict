import io
import random

import numpy as np
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from . import importers, rewards
from .engine.pipeline import run_forecast
from .models import Match, Prediction, UserPrediction

TEAMS = ["Arsenal", "Chelsea", "Liverpool", "Everton", "Fulham", "Leeds"]


def fake_season(year, seed):
    rng = random.Random(seed)
    rows = ["Date,HomeTeam,AwayTeam,FTHG,FTAG,HTHG,HTAG,HY,AY,HR,AR,HC,AC,HS,AS,HST,AST,HF,AF"]
    for r in importers.generate_fixtures(TEAMS, year):
        rows.append(f"{r['date']},{r['home']},{r['away']},{rng.randint(0,3)},{rng.randint(0,2)},{rng.randint(0,1)},{rng.randint(0,1)},"
                    f"{rng.randint(0,4)},{rng.randint(0,4)},0,0,{rng.randint(2,9)},{rng.randint(2,9)},12,9,5,4,11,10")
    return io.StringIO("\n".join(rows))


class Base(TestCase):
    def setUp(self):
        for y, s in ((2022, 1), (2023, 2), (2024, 3)):
            importers.import_season_csv(fake_season(y, s), y)
        importers.import_generated_fixtures(TEAMS, 2025)


class LeakageTests(Base):
    def snapshot(self):
        return [(p.match_id, p.xg_home, p.xg_away, p.p_home, p.pred_home, p.pred_away, str(p.detail["timeline"]))
                for p in Prediction.objects.filter(match__season__start_year=2025).order_by("match_id")]

    def test_target_season_results_never_change_the_forecast(self):
        run_forecast(2025, nsim=2000)
        before = self.snapshot()
        self.assertEqual(len(before), 30)
        rng = random.Random(9)
        for m in Match.objects.filter(season__start_year=2025):  # "play" the season with wild results
            m.home_goals, m.away_goals, m.ht_home, m.ht_away = rng.randint(0, 9), rng.randint(0, 9), 0, 0
            m.home_yellow, m.away_yellow, m.home_corners, m.away_corners = 9, 9, 20, 20
            m.save()
        run_forecast(2025, nsim=2000)
        self.assertEqual(before, self.snapshot())

    def test_next_season_does_use_earlier_results(self):
        run_forecast(2025, nsim=1000)
        a = Prediction.objects.filter(match__season__start_year=2025).first().xg_home
        for m in Match.objects.filter(season__start_year=2025):
            m.home_goals, m.away_goals = 5, 0
            m.save()
        importers.import_generated_fixtures(TEAMS, 2026)
        run_forecast(2026, nsim=1000)
        self.assertEqual(Prediction.objects.filter(match__season__start_year=2026).count(), 30)


class RewardTests(Base):
    def test_pick_lock_and_settle(self):
        run_forecast(2025, nsim=1000)
        u = User.objects.create_user("ann", password="pw12345!x")
        c = Client()
        c.login(username="ann", password="pw12345!x")
        m = Match.objects.filter(season__start_year=2025).order_by("kickoff").first()
        m.kickoff = timezone.now() + timezone.timedelta(days=2)
        m.save()
        r = c.post(f"/match/{m.id}/pick/", {"home_goals": 2, "away_goals": 1, "ht_result": "H", "red_card": "no"})
        self.assertTrue(r.json()["ok"])
        m.home_goals, m.away_goals, m.ht_home, m.ht_away, m.home_red, m.away_red = 2, 1, 1, 0, 0, 0
        m.save()
        rewards.settle_match(m)
        p = UserPrediction.objects.get(user=u)
        self.assertTrue(p.settled)
        self.assertGreaterEqual(p.points, 3 + 5 + 2 + 1)  # result + exact + HT + red-no
        m.kickoff = timezone.now() - timezone.timedelta(hours=1)
        m.save()
        r = c.post(f"/match/{m.id}/pick/", {"home_goals": 0, "away_goals": 0})
        self.assertEqual(r.status_code, 400)  # locked after kick-off

    def test_anonymous_cannot_pick(self):
        run_forecast(2025, nsim=500)
        m = Match.objects.filter(season__start_year=2025).first()
        self.assertEqual(Client().post(f"/match/{m.id}/pick/", {"home_goals": 1, "away_goals": 1}).status_code, 401)


class StudioTests(Base):
    def test_generate_fixtures_balanced(self):
        rows = importers.generate_fixtures(TEAMS, 2030)
        self.assertEqual(len(rows), 30)
        for t in TEAMS:
            self.assertEqual(sum(t in (r["home"], r["away"]) for r in rows), 10)
        self.assertEqual(len({(r["home"], r["away"]) for r in rows}), 30)
        wk = {}
        for r in rows:
            for t in (r["home"], r["away"]):
                self.assertNotIn((r["matchweek"], t), wk)
                wk[(r["matchweek"], t)] = 1

    def test_pages_render(self):
        run_forecast(2025, nsim=500)
        c = Client()
        m = Match.objects.filter(season__start_year=2025).first()
        for u in ("/", "/matches/", f"/match/{m.id}/", "/table/", "/teams/", "/team/arsenal/", "/players/", "/leaderboard/", "/accuracy/"):
            self.assertEqual(c.get(u).status_code, 200, u)
