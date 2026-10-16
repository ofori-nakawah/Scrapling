"""Live match stats from Sportradar (the feeds behind SportyBet's live match tracker), via Scrapling.

    python live_stats.py <minute> sr:match:1 [sr:match:2 ...]     # prints one JSON document

Per match: red cards, shots on target, dangerous-attack share (whole match), and from the timeline the
shots and shots on target in the last 10 minutes before <minute>. Needs .gismo-token.json (mint_token.py).
"""
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, "/work")
from scrapling.fetchers import Fetcher
from mint_token import GISMO, UA, TOKEN_FILE

H = {"user-agent": UA, "referer": "https://www.sportybet.com/"}
RECENT = 10


def feed(path, tok):
    for attempt in range(3):
        try:
            doc = Fetcher.get(f"{GISMO}/{path}?{tok}", headers=H, timeout=20).json()["doc"][0]
            return None if doc.get("event") == "exception" else doc.get("data")
        except Exception:
            time.sleep(1 + attempt)
    return None


def pair(vals, key):
    v = (vals.get(key) or {}).get("value") or {}
    try:
        return int(v.get("home") or 0), int(v.get("away") or 0)
    except (TypeError, ValueError):
        return 0, 0


def one(mid, minute, tok):
    n = mid.split(":")[-1]
    ext = feed(f"match_detailsextended/{n}", tok)
    if not isinstance(ext, dict):
        return mid, None
    vals = ext.get("values") or {}
    red = pair(vals, "50")
    yr = pair(vals, "45")
    out = {"red": [red[0] + yr[0], red[1] + yr[1]], "sot": list(pair(vals, "125")),
           "da_pct": list(pair(vals, "dangerousattackpercentage")), "da": list(pair(vals, "1029"))}
    tl = feed(f"match_timeline/{n}", tok)
    recent = {"shots": [0, 0], "sot": [0, 0]}
    if isinstance(tl, dict):
        for e in tl.get("events") or []:
            t, side = e.get("time"), e.get("team")
            if side not in ("home", "away") or not isinstance(t, int) or t < minute - RECENT:
                continue
            i = 0 if side == "home" else 1
            if e.get("type") in ("shotontarget", "shotofftarget", "shotblocked"):
                recent["shots"][i] += 1
            if e.get("type") == "shotontarget":
                recent["sot"][i] += 1
    out["recent"] = recent
    return mid, out


if __name__ == "__main__":
    minute = int(sys.argv[1])
    tok = json.load(open(TOKEN_FILE))["token"]
    ids = [i for i in sys.argv[2:] if len(i) < 20]          # simulated ids have no Sportradar match
    with ThreadPoolExecutor(6) as ex:
        print(json.dumps(dict(ex.map(lambda i: one(i, minute, tok), ids))))
