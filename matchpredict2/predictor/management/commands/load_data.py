import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from predictor import importers


class Command(BaseCommand):
    help = "Load the 20 historical seasons, the 26/27 fixture list (no results) and the 26/27 rosters."

    def add_arguments(self, p):
        p.add_argument("--replace", action="store_true", help="re-import seasons that already exist")
        p.add_argument("--target", type=int, default=settings.TARGET_SEASON)

    def handle(self, *a, **o):
        d = Path(settings.DATA_DIR)
        for f in sorted((d / "raw").glob("season-*.csv")):
            code = re.search(r"season-(\d\d)(\d\d)", f.name)
            year = 2000 + int(code.group(1)) if int(code.group(1)) < 90 else 1900 + int(code.group(1))
            r = importers.import_season_csv(str(f), year, replace=o["replace"])
            self.stdout.write(f"{r['season']}: {'skipped' if r['skipped'] else str(r['created']) + ' matches'}")
        t = o["target"]
        fx = d / f"fixtures_{str(t)[2:]}{str(t + 1)[2:]}.csv"
        if fx.exists():
            try:
                self.stdout.write(str(importers.import_fixtures_csv(str(fx), t, replace=o["replace"])))
            except ValueError as e:
                self.stdout.write(f"fixtures: {e}")
        pl = d / "players" / f"players_{str(t)[2:]}{str(t + 1)[2:]}.csv"
        if pl.exists():
            self.stdout.write(str(importers.import_players_csv(str(pl), t)))
