"""Apply confirmed summer-2026 moves (incl. deadline day, 1 Sept) to the 2026/27 roster file.
Identity-only edits: club changes and removals. No 2026/27 performance data is touched.
Each rule: (current club, regex on 'first second name', new club or None = left the league)."""
import re, sys
import pandas as pd

MOVES = [
 # between Premier League clubs (club at snapshot -> club on 1 Sept)
 ("Chelsea","Nicolas Jackson","Aston Villa"),("Chelsea","Garnacho","Aston Villa"),
 ("Chelsea","Liam Delap","Nott'm Forest"),("Man City","Omar Marmoush","Tottenham"),
 ("Man City","Jack Grealish","Everton"),("Chelsea","Enzo Fern","Man City"),
 ("Everton","Iliman Ndiaye","Man City"),("Chelsea","Mykhailo Mudryk","Tottenham"),
 ("Chelsea","Tosin","Tottenham"),("Chelsea","Andrey Santos","Man United"),
 ("Chelsea","Tyrique George","Everton"),("Chelsea","Axel Disasi","Crystal Palace"),
 ("Tottenham","Kevin Danso","Sunderland"),("Aston Villa","Guessand","Crystal Palace"),
 ("Arsenal","N.rgaard","Everton"),("Man City","Stephen Mfuni","Coventry"),
 ("Everton","Tim Iroegbunam","Hull"),("Everton","Dwight McNeil","Crystal Palace"),
 ("Crystal Palace","Brennan Johnson","Everton"),("Newcastle","Targett","Hull"),
 ("Leeds","Joe Gelhardt","Hull"),("Leeds","Illan Meslier","Arsenal"),
 ("Leeds","Karl Darlow","Man United"),("Man City","Nico Gonz","Newcastle"),
 ("Man City","Savinho","Tottenham"),("Man City","James Trafford","Leeds"),
 ("Fulham","Harry Wilson","Leeds"),("Aston Villa","Emiliano Mart","Chelsea"),
 ("Fulham","Sa.a Luki","Ipswich"),("Fulham","Issa Diop","Ipswich"),
 ("Tottenham","Vu.kovi","Brighton"),("Leeds","Struijk","Brighton"),
 ("Crystal Palace","Lacroix","Chelsea"),("Brighton","Danny Welbeck","Chelsea"),
 ("Brentford","Jordan Henderson","Chelsea"),("Nott'm Forest","Elliot Anderson","Man City"),
 ("Nott'm Forest","Awoniyi","Coventry"),
 # left the league (permanent, loan out or to a non-PL club)
 ("Aston Villa","Ollie Watkins",None),("Arsenal","Gabriel Martinelli",None),
 ("Arsenal","Gabriel Fernando de Jesus",None),("Arsenal","F.bio Vieira",None),
 ("Arsenal","Ethan Nwaneri",None),("Arsenal","Kiwior",None),("Arsenal","Trossard",None),
 ("Liverpool","Mohamed Salah",None),("Liverpool","Konat.",None),("Liverpool","Harvey Elliott",None),
 ("Newcastle","Anthony Gordon",None),("Newcastle","Woltemade",None),("Newcastle","Trippier",None),
 ("Man City","Rodri",None),("Man City","Reijnders",None),("Man City","John Stones",None),
 ("Man City","Bernardo",None),("Man City","Ak.",None),("Chelsea","Cucurella",None),
 ("Chelsea","Marc Guiu",None),("Chelsea","Robert S.nchez",None),("Chelsea","Essugo",None),
 ("Chelsea","Deivid Washington",None),("Everton","Norberto Bercique",None),
 ("Everton","Nathan Patterson",None),("Man United","H.jlund",None),("Man United","Casemiro",None),
 ("Man United","Malacia",None),("Tottenham","Cristian Romero",None),("Tottenham","Pape Matar Sarr",None),
 ("Tottenham","Kota Takai",None),("Crystal Palace","Borna Sosa",None),("Leeds","Wilfried Gnonto",None),
 ("Aston Villa","Donyell Malen",None),("Aston Villa","Digne",None),("Fulham","Raúl Jim.nez",None),
]

def main(path):
    d = pd.read_csv(path)
    full = (d["first_name"].fillna("") + " " + d["second_name"].fillna("") + " " + d["name"].fillna(""))
    drop, log = [], []
    for club, rx, new in MOVES:
        hit = d[(d["club"] == club) & full.str.contains(rx, case=False, regex=True)]
        if len(hit) == 0:
            log.append(f"  not in roster: {club} / {rx}"); continue
        if len(hit) > 1:
            log.append(f"  AMBIGUOUS {club} / {rx}: {list(hit['name'])}"); continue
        i = hit.index[0]
        if new is None: drop.append(i)
        else: d.loc[i, "club"] = new
        log.append(f"  {hit.iloc[0]['name']:<14} {club} -> {new or 'left league'}")
    d = d.drop(index=drop)
    d.to_csv(path, index=False)
    print("\n".join(log)); print(len(d), "players"); print(d["club"].value_counts().to_string())

if __name__ == "__main__":
    main(sys.argv[1])
