"""One snapshot of every live football game on SportyBet (via Scrapling): minute, score and the
open markets the scoreboard favours, each with the goals that would have to come to lose it.
Prints one JSON document. Read-only."""
import json, math, re, sys, time
sys.path.insert(0, "/work")
from scrape_today import pages, odds


def minute(ev):
    m = re.match(r"(\d+):", ev.get("playedSeconds") or "")
    return int(m.group(1)) if m else None


def picks(h, a, px):
    """[(name, leg, price, goals_to_lose)] for markets the current score already favours."""
    d, lead = abs(h - a), "home" if h > a else "away" if a > h else None
    opts = []
    if lead:
        opts += [(f"{lead.title()} win", ("1", "", "1" if lead == "home" else "3"), d),
                 (f"{lead.title()} or draw", ("10", "", "9" if lead == "home" else "11"), d + 1)]
    for line in ("0.5", "1.5", "2.5", "3.5", "4.5", "5.5"):
        if h + a < float(line):
            opts.append((f"Under {line}", ("18", f"total={line}", "13"), math.floor(float(line)) - (h + a) + 1))
    if h == 0 or a == 0:
        opts.append(("Both teams NOT to score", ("29", "", "76"), 1))
    return [(n, leg, px[leg], need) for n, leg, need in opts if leg in px]


games = []
for data in pages():
    for t in data.get("tournaments", []):
        for ev in t.get("events", []):
            if ev.get("status") != 1:
                continue
            try:
                h, a = map(int, (ev.get("setScore") or "").split(":"))
            except ValueError:
                h = a = None
            cat = ev["sport"]["category"]
            ps = picks(h, a, odds(ev)) if h is not None else []
            px_all = odds(ev) if h is not None else {}
            games.append({"prices": {"|".join(k): v for k, v in px_all.items()}, "event": ev["eventId"], "minute": minute(ev), "period": ev.get("matchStatus"),
                          "score": ev.get("setScore"), "home": ev["homeTeamName"], "away": ev["awayTeamName"],
                          "league": f"{cat['name']} · {cat['tournament']['name']}",
                          "picks": sorted(({"name": n, "leg": "/".join([ev["eventId"], *leg]), "price": p, "goals_to_lose": g}
                                           for n, leg, p, g in ps), key=lambda x: x["price"])})
print(json.dumps({"at": int(time.time()), "games": games}))
