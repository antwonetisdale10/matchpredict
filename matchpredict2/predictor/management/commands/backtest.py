from django.core.management.base import BaseCommand

from predictor.engine.pipeline import run_backtest


class Command(BaseCommand):
    help = "Walk-forward backtest of the goal model on past seasons (stored for the Accuracy page)."

    def add_arguments(self, p):
        p.add_argument("--first", type=int, default=2015)
        p.add_argument("--gap", type=int, default=1, help="seasons of recent data hidden (1 = like 25/26 is missing)")

    def handle(self, *a, **o):
        done = run_backtest(first=o["first"], gap=o["gap"])
        self.stdout.write(self.style.SUCCESS(f"backtested {len(done)} seasons: {done[0]}-{done[-1]}"))
