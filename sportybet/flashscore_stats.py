"""Live match stats from Flashscore, for games Sportradar does not cover (the fallback in live_monitor).

    python flashscore_stats.py /work/out/live/fs-in.json      # prints one JSON document

Input: [{"event", "home", "away", "score"}, ...] (SportyBet's names and score). Each game is found on
Flashscore's live list by team names, and accepted only when the score there is the same, so a wrong
match cannot pass. Its statistics page then gives possession, shots, shots on target, corners and,
when the league has them, red cards and dangerous attacks. Flashscore has no shot timeline: the
monitor works out recent pressure from its own earlier readings.
"""
import json, re, sys, unicodedata
from scrapling.fetchers import DynamicFetcher

LIVE = "https://www.flashscore.com.gh/live/"
NOISE = {"fc", "cf", "sc", "cd", "ca", "ac", "club", "de", "del", "la", "el", "the", "afc", "csd", "ad", "ec",
         "sp", "rj", "fk", "if", "sk", "united", "city", "deportivo", "atletico", "sociedad", "real", "u19", "u20", "u21", "u23",
         "reserve", "reserves", "ii", "b", "women"}


def words(name):
    t = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return {w for w in re.findall(r"[a-z0-9]+", t) if w not in NOISE and len(w) > 1}


def overlap(a, b):
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def live_rows():
    p = DynamicFetcher.fetch(LIVE, headless=True, network_idle=True, timeout=60000)
    rows = []
    for r in p.css("[id^='g_1_']"):
        link = r.css("a.eventRowLink")
        if not link:
            continue
        label = link[0].attrib.get("aria-label", "")
        if " - " not in label:
            continue
        home, away = label.split(" - ", 1)
        texts = [t.strip() for t in r.css("::text").getall() if t.strip()]
        nums = [t for t in texts if t.isdigit()]
        score = f"{nums[-2]}:{nums[-1]}" if len(nums) >= 2 else None
        rows.append({"href": link[0].attrib.get("href", ""), "home": home, "away": away, "score": score})
    return rows


def stats_page(href):
    base, _, query = href.partition("?")
    p = DynamicFetcher.fetch(base.rstrip("/") + "/summary/stats/overall/?" + query, headless=True,
                             network_idle=True, timeout=60000)
    vals = {}
    for r in p.css("[data-testid='wcl-statistics']"):
        t = [x.strip() for x in r.css("::text").getall() if x.strip()]
        if len(t) >= 3:
            vals.setdefault(t[1].lower(), (t[0], t[-1]))
    return vals


def num(v):
    m = re.search(r"\d+", v or "")
    return int(m.group()) if m else 0


def to_stats(vals):
    if not vals:
        return None
    pair = lambda k: [num(vals[k][0]), num(vals[k][1])] if k in vals else None   # noqa: E731
    da = pair("dangerous attacks")
    return {"source": "flashscore", "red": pair("red cards") or [0, 0], "sot": pair("shots on target") or [0, 0],
            "da": da, "da_pct": [round(100 * x / max(1, sum(da))) for x in da] if da else None,
            "possession": pair("ball possession"), "recent": None}


def main():
    games = json.load(open(sys.argv[1]))
    rows = live_rows()
    out = {}
    for g in games:
        best, best_score = None, 0.0
        for r in rows:
            s = min(overlap(g["home"], r["home"]), overlap(g["away"], r["away"]))
            if s > best_score:
                best, best_score = r, s
        if not best or best_score < 0.5 or best["score"] != g["score"]:
            out[g["event"]] = None            # no confident match (names or score differ)
            continue
        out[g["event"]] = to_stats(stats_page(best["href"]))
        if out[g["event"]]:
            out[g["event"]]["flashscore"] = f"{best['home']} v {best['away']}"
    print(json.dumps(out))


if __name__ == "__main__":
    main()
