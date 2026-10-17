"""Convert an openfootball-style fixture text file into fixtures.csv.

SAFETY: this deliberately keeps ONLY matchweek / date / time / home / away.
Any score text on a line is thrown away, so played results can never leak
into the predictor.

usage: python3 openfootball_to_fixtures.py in.txt out.csv 2026
       (last arg = calendar year the season STARTS in)
"""

import csv
import re
import sys
from datetime import datetime

NAMES = {
    "Arsenal FC": "Arsenal",
    "Aston Villa FC": "Aston Villa",
    "AFC Bournemouth": "Bournemouth",
    "Brentford FC": "Brentford",
    "Brighton & Hove Albion FC": "Brighton",
    "Chelsea FC": "Chelsea",
    "Coventry City FC": "Coventry",
    "Crystal Palace FC": "Crystal Palace",
    "Everton FC": "Everton",
    "Fulham FC": "Fulham",
    "Hull City AFC": "Hull",
    "Ipswich Town FC": "Ipswich",
    "Leeds United FC": "Leeds",
    "Liverpool FC": "Liverpool",
    "Manchester City FC": "Man City",
    "Manchester United FC": "Man United",
    "Newcastle United FC": "Newcastle",
    "Nottingham Forest FC": "Nott'm Forest",
    "Sunderland AFC": "Sunderland",
    "Tottenham Hotspur FC": "Tottenham",
}
SCORE = re.compile(r"\s+\d+-\d+(\s+\(\d+-\d+\))?\s*$")
MD = re.compile(r"Matchday\s+(\d+)")
DATE = re.compile(r"^\s+(?:\w{3}\s+)?(\w{3})\s+(\d{1,2})(?:\s+(\d{4}))?\s*$")
MATCH = re.compile(r"^\s+(?:(\d{1,2}:\d{2})\s+)?(.+?)\s+v\s+(.+?)\s*$")


def main(src, dst, start_year):
    rows, week, cur, last_month = [], 0, None, None
    year = int(start_year)
    for line in open(src, encoding="utf-8"):
        line = line.rstrip("\n")
        if m := MD.search(line):
            week = int(m.group(1))
            continue
        if m := DATE.match(line):
            mon = datetime.strptime(m.group(1), "%b").month
            if last_month is not None and mon < last_month:
                year += 1
            last_month = mon
            cur = datetime(year, mon, int(m.group(2))).date()
            continue
        if cur and (m := MATCH.match(SCORE.sub("", line))):
            t, h, a = m.groups()
            rows.append(
                {
                    "matchweek": week,
                    "date": cur.isoformat(),
                    "time": t or "15:00",
                    "home": NAMES.get(h.strip(), h.strip()),
                    "away": NAMES.get(a.strip(), a.strip()),
                }
            )
    with open(dst, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["matchweek", "date", "time", "home", "away"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} fixtures -> {dst}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
