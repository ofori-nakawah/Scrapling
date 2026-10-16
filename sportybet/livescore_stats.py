"""Livescore's view of live games (its public JSON API, no browser): the live-status check in live_monitor.

    python livescore_stats.py /work/out/live/ls-in.json      # prints one JSON document

Input: [{"event", "home", "away", "score"}, ...] (SportyBet's names and score). Each game is found on
Livescore's live list by team names (both teams must match). Output per game, or null when Livescore does not
list it: {"livescore": names, "score": "h:a", "minute", "red": [h, a]} from its incidents, plus "stats" (shots
on target, possession) when the league has live statistics.
"""
import json, re, sys, unicodedata
from concurrent.futures import ThreadPoolExecutor
from scrapling.fetchers import Fetcher

LIVE = "https://prod-public-api.livescore.com/v1/api/app/live/soccer/0?locale=en"
API = "https://prod-cdn-public-api.livescore.com/v1/api/app/{}/soccer/{}?locale=en"
RED = {44, 45}                            # incident types: second yellow, straight red
NOISE = {"fc", "cf", "sc", "cd", "ca", "ac", "club", "de", "del", "la", "el", "the", "afc", "csd", "ad", "ec",
         "sp", "fk", "if", "sk", "united", "city", "deportivo", "atletico", "sociedad", "real", "u19", "u20", "u21",
         "u23", "reserve", "reserves", "ii", "b", "women", "youth"}


def words(name):
    t = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return {w for w in re.findall(r"[a-z0-9]+", t) if w not in NOISE and len(w) > 1}


def get(kind, eid):
    try:
        return Fetcher.get(API.format(kind, eid), timeout=15).json() or {}
    except Exception:  # noqa: BLE001
        return {}


def read(e):
    red = [0, 0]
    for per in (get("incidents", e["Eid"]).get("Incs") or {}).values():
        for x in per:
            for y in [x] + (x.get("Incs") or []):
                if y.get("IT") in RED and y.get("Nm") in (1, 2):
                    red[y["Nm"] - 1] += 1
    out = {"livescore": f"{e['T1'][0]['Nm']} v {e['T2'][0]['Nm']}", "score": f"{e.get('Tr1') or 0}:{e.get('Tr2') or 0}",
           "minute": e.get("Eps"), "red": red}
    st = get("statistics", e["Eid"]).get("Stat") or []
    if len(st) == 2 and any((x.get("Shon") or 0) + (x.get("Shof") or 0) for x in st):
        n = lambda k: [int(st[0].get(k) or 0), int(st[1].get(k) or 0)]   # noqa: E731
        out["stats"] = {"source": "livescore", "red": red, "sot": n("Shon"), "da": None, "da_pct": None,
                        "possession": n("Pss"), "recent": None}
    return out


def main():
    games = json.load(open(sys.argv[1]))
    try:
        ev = [e for s in Fetcher.get(LIVE, timeout=15).json().get("Stages") or [] for e in s.get("Events") or []]
    except Exception:  # noqa: BLE001
        ev = []
    found = {}
    for g in games:
        h, a = words(g["home"]), words(g["away"])
        best = None
        for e in ev:
            sh, sa = len(h & words(e["T1"][0]["Nm"])), len(a & words(e["T2"][0]["Nm"]))
            if sh and sa and (not best or sh + sa > best[0]):
                best = (sh + sa, e)
        found[g["event"]] = best[1] if best else None
    with ThreadPoolExecutor(8) as ex:
        res = dict(zip(found, ex.map(lambda e: read(e) if e else None, found.values())))
    print(json.dumps(res))


if __name__ == "__main__":
    main()
