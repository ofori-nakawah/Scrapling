"""Find ONE live football pick at 1.01 in the 80th-89th minute and book it as a single.
Prints one JSON line {"code", "pick", ...} when found; prints nothing when not. Nothing is staked here."""
import json, math, re, sys
sys.path.insert(0, "/work")
from scrape_today import pages, odds
from book_live import book

TARGET, LO, HI = 1.01, 80, 89


def minute(ev):
    m = re.match(r"(\d+):", ev.get("playedSeconds") or "")
    return int(m.group(1)) if m else None


cands = []
for data in pages():
    for t in data.get("tournaments", []):
        for ev in t.get("events", []):
            mins = minute(ev)
            if ev.get("status") != 1 or mins is None or not LO <= mins <= HI or ev.get("matchStatus") != "H2":
                continue
            try:
                h, a = map(int, (ev.get("setScore") or "").split(":"))
            except ValueError:
                continue
            px, d = odds(ev), abs(h - a)
            lead = "home" if h > a else "away" if a > h else None
            opts = []   # (goals the other way needed to lose, name, leg)
            if lead:
                opts.append((d, f"{lead.title()} win", ("1", "", "1" if lead == "home" else "3")))
                opts.append((d + 1, f"{lead.title()} or draw", ("10", "", "9" if lead == "home" else "11")))
            for line in ("0.5", "1.5", "2.5", "3.5", "4.5", "5.5"):
                if h + a < float(line):
                    opts.append((math.floor(float(line)) - (h + a) + 1, f"Under {line}", ("18", f"total={line}", "13")))
            if h == 0 or a == 0:
                opts.append((1, "Both teams NOT to score", ("29", "", "76")))
            for need, name, leg in opts:
                price = px.get(leg)
                if price is not None and price <= TARGET:
                    cands.append((need, mins, price, name, leg, ev))

if not cands:
    sys.exit(0)
need, mins, price, name, leg, ev = sorted(cands, key=lambda c: (-c[0], -c[1]))[0]
mid, spec, out = leg
code = book([f"{ev['eventId']}/{mid}/{spec}/{out}"])
print(json.dumps({"code": code, "pick": name, "price": price, "minute": mins, "score": ev["setScore"],
                  "match": f"{ev['homeTeamName']} v {ev['awayTeamName']}", "goals_to_lose": need, "event": ev["eventId"]}))
