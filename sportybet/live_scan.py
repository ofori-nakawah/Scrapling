"""One snapshot of every live football game on SportyBet (via Scrapling): minute, score and the
open markets the scoreboard favours, each with the goals that would have to come to lose it.
Prints one JSON document. Read-only.

Live games come from configurableLiveOrPrematchEvents, the feed behind SportyBet's live screen
(m/sport/football/live_list). The paginated odds feed (wapConfigurableEventsByOrder) is not a live
list: on 2026-10-15 it carried 4 of the 10 games live at the time. The live feed carries only a few
markets, so games in or near a betting window get their full market list from factsCenter/event.
"""
import json, math, re, sys, time
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, "/work")
from scrape_today import odds
from scrapling.fetchers import Fetcher

API = "https://www.sportybet.com/api/gh/factsCenter/"
H = {"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
     "accept": "application/json"}
NEAR_1H, NEAR_2H = 30, 75        # fetch every market from these minutes (the windows open at 35' and 80')


def get(path):
    for attempt in range(3):
        try:
            d = Fetcher.get(API + path, headers=H, timeout=30).json()
            if d.get("bizCode") == 10000:
                return d.get("data")
        except Exception:
            pass
        time.sleep(1 + attempt)
    return None


def live_events():
    data = get("configurableLiveOrPrematchEvents?sportId=sr%3Asport%3A1&withTwoUpMarket=true&withOneUpMarket=true") or []
    tours = data if isinstance(data, list) else data.get("tournaments", [])
    out = []
    for t in tours:
        for ev in t.get("events") or []:
            if ev.get("status") == 1:
                ev.setdefault("_tournament", t.get("name", ""))
                out.append(ev)
    return out


def outcomes(ev):
    """Every open outcome of every market: [market id, specifier, outcome id, market name, outcome name, price].
    A live game lists some markets twice (live and pre-match copies); the open copy wins."""
    best = {}
    for m in ev.get("markets") or []:
        k = (m["id"], m.get("specifier") or "")
        if k in best and best[k].get("status") == 0:
            continue
        best[k] = m
    out = []
    for (mid, spec), m in best.items():
        if m.get("status") not in (0, None):
            continue
        for o in m.get("outcomes") or []:
            try:
                price = float(o["odds"])
            except (KeyError, TypeError, ValueError):
                continue
            if o.get("isActive", 1) and price > 1.0:
                out.append([mid, spec, o["id"], m.get("desc") or "", o.get("desc") or "", price])
    return out


def near(ev):
    m = minute(ev)
    return m is not None and ((ev.get("matchStatus") == "H1" and m >= NEAR_1H) or (ev.get("matchStatus") == "H2" and m >= NEAR_2H))


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


events = live_events()
want = [ev for ev in events if near(ev)]
with ThreadPoolExecutor(6) as ex:
    details = dict(zip((ev["eventId"] for ev in want), ex.map(lambda ev: get(f"event?eventId={ev['eventId']}"), want)))
games = []
for ev in events:
    full = details.get(ev["eventId"])
    if full and full.get("markets"):
        ev = {**ev, "markets": full["markets"], "setScore": full.get("setScore", ev.get("setScore")),
              "playedSeconds": full.get("playedSeconds", ev.get("playedSeconds")),
              "matchStatus": full.get("matchStatus", ev.get("matchStatus"))}
    if True:
        if True:
            try:
                h, a = map(int, (ev.get("setScore") or "").split(":"))
            except ValueError:
                h = a = None
            cat = ev.get("sport", {}).get("category") or {"name": "", "tournament": {"name": ev.get("_tournament", "")}}
            ps = picks(h, a, odds(ev)) if h is not None else []
            px_all = odds(ev) if h is not None else {}
            halves = []
            for x in ev.get("gameScore") or []:
                try:
                    halves.append(tuple(map(int, x.split(":"))))
                except ValueError:
                    pass
            games.append({"prices": {"|".join(k): v for k, v in px_all.items()}, "event": ev["eventId"],
                          "halves": halves, "outcomes": outcomes(ev) if full else [], "minute": minute(ev), "period": ev.get("matchStatus"),
                          "score": ev.get("setScore"), "home": ev["homeTeamName"], "away": ev["awayTeamName"],
                          "league": f"{cat['name']} · {cat['tournament']['name']}",
                          "picks": sorted(({"name": n, "leg": "/".join([ev["eventId"], *leg]), "price": p, "goals_to_lose": g}
                                           for n, leg, p, g in ps), key=lambda x: x["price"])})
print(json.dumps({"at": int(time.time()), "games": games}))
