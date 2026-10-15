"""Live football near full time on SportyBet (via Scrapling): score, minute, and the open
markets the scoreboard already favours heavily. Read-only; nothing is booked."""
import re, sys
sys.path.insert(0, "/work")
from scrape_today import pages, odds

MIN_MINUTE = int(sys.argv[1]) if len(sys.argv) > 1 else 75


def minute(ev):
    m = re.match(r"(\d+):", ev.get("playedSeconds") or "")
    return int(m.group(1)) if m else None


rows = []
for data in pages():
    for t in data.get("tournaments", []):
        for ev in t.get("events", []):
            if ev.get("status") != 1:
                continue
            mins = minute(ev)
            if mins is None or mins < MIN_MINUTE:
                continue
            try:
                h, a = map(int, (ev.get("setScore") or "").split(":"))
            except ValueError:
                continue
            px = odds(ev)
            lead = "home" if h > a else "away" if a > h else None
            picks = []
            if lead:
                win = px.get(("1", "", "1" if lead == "home" else "3"))
                dc = px.get(("10", "", "9" if lead == "home" else "11"))
                if win: picks.append((f"{lead.title()} win", win))
                if dc: picks.append((f"{lead.title()} or draw", dc))
            for line in ("0.5", "1.5", "2.5", "3.5", "4.5", "5.5"):
                u = px.get(("18", f"total={line}", "13"))
                if u and h + a < float(line):
                    picks.append((f"Under {line}", u))
            nogg = px.get(("29", "", "76"))
            if nogg and (h == 0 or a == 0):
                picks.append(("Both teams NOT to score", nogg))
            picks = sorted(picks, key=lambda p: p[1])
            cat = ev["sport"]["category"]
            rows.append((mins, abs(h - a), ev["eventId"], f"{cat['name']} · {cat['tournament']['name']}",
                         ev["homeTeamName"], ev["awayTeamName"], f"{h}:{a}", ev.get("matchStatus"), picks))

rows.sort(key=lambda r: (-r[1], -r[0]))
print(f"{len(rows)} live games at {MIN_MINUTE}'+")
for mins, diff, eid, lg, home, away, score, st, picks in rows:
    p = "  ".join(f"{n} @{o:.2f}" for n, o in picks[:4]) or "(markets suspended)"
    print(f"{mins:>3}' {score:>5}  {home[:26]} v {away[:26]}  [{lg[:40]}] {eid}\n        {p}")
