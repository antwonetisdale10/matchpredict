from django.conf import settings
from django.core.management.base import BaseCommand

from predictor.engine.pipeline import run_forecast


class Command(BaseCommand):
    help = "Predict every fixture of a season using ONLY earlier seasons' results."

    def add_arguments(self, p):
        p.add_argument("--season", type=int, default=settings.TARGET_SEASON, help="start year, e.g. 2026 for 26/27")
        p.add_argument("--nsim", type=int, default=20000)

    def handle(self, *a, **o):
        r = run_forecast(o["season"], progress=lambda i, n: self.stdout.write(f"  {i}/{n}", ending="\r"), nsim=o["nsim"])
        self.stdout.write(self.style.SUCCESS(f"\n{r['season']}: {r['matches']} matches predicted | {r['version']}"))
        self.stdout.write(f"newcomer-prior teams: {', '.join(r['newcomers']) or '-'}")
