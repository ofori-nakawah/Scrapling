"""Goal / over-under stats for every game in out/games-<date>.json, scraped with
Scrapling from Sportradar's gismo feeds (the data behind SportyBet's Stats tab).

    docker run --rm -v "$PWD:/work" --entrypoint uv scrapling:local run python /work/goal_stats.py [YYYY-MM-DD]

Needs .gismo-token.json (mint_token.py makes one; it is re-minted here when
missing or under an hour from expiry). Writes out/goals-<date>.{json,csv,html}.
"""
import csv, datetime as dt, html, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scrapling.fetchers import Fetcher
from mint_token import GISMO, UA, TOKEN_FILE, mint

HERE = os.path.dirname(os.path.abspath(__file__))
LAST_N, VENUE_N, H2H_N = 10, 8, 10
LINES = (0.5, 1.5, 2.5, 3.5)


def token(games):
    try:
        t = json.load(open(TOKEN_FILE))
        if t["exp"] - time.time() > 3600:
            return t["token"]
    except (OSError, ValueError, KeyError):
        pass
    # Only a pre-match page with real (Sportradar-mapped) ids carries a Stats tab.
    ids = [g["event_id"] for g in games if g["status"] == "upcoming" and len(g["event_id"]) < 20]
    t = mint(ids); json.dump(t, open(TOKEN_FILE, "w"))
    return t["token"]


TOKEN = None


@lru_cache(maxsize=None)
def gismo(path):
    """data for one feed, {} on Sportradar's HTTP-200 'exception' or a list-shaped empty."""
    for attempt in range(3):
        try:
            d = Fetcher.get(f"{GISMO}/{path}?{TOKEN}", timeout=30,
                            headers={"user-agent": UA, "referer": "https://www.sportybet.com/"}).json()
            doc = d["doc"][0]
            if doc.get("event") == "exception":
                return {}
            return doc["data"] if isinstance(doc["data"], dict) else {}
        except Exception:
            time.sleep(1 + attempt)
    return {}


def pct(xs):
    return round(100 * sum(xs) / len(xs)) if xs else None


def avg(xs):
    return round(sum(xs) / len(xs), 2) if xs else None


def team_rows(uid):
    """Recent matches newest first, from this team's side: gf, ga, venue, first-half goals."""
    out = []
    for m in gismo(f"stats_team_lastx/{uid}").get("matches") or []:
        res, teams = m.get("result") or {}, m.get("teams") or {}
        if res.get("home") is None or res.get("away") is None:
            continue
        if str((teams.get("home") or {}).get("uid")) == str(uid): us, them, v = "home", "away", "H"
        elif str((teams.get("away") or {}).get("uid")) == str(uid): us, them, v = "away", "home", "A"
        else: continue
        p1 = (m.get("periods") or {}).get("p1") or {}
        fh = p1[us] + p1[them] if p1.get(us) is not None and p1.get(them) is not None else None
        out.append({"gf": res[us], "ga": res[them], "venue": v, "fh": fh})
    return out


def summarise(rows):
    tot = [r["gf"] + r["ga"] for r in rows]
    fh = [r["fh"] for r in rows if r["fh"] is not None]
    s = {"n": len(rows), "avg_goals": avg(tot), "avg_scored": avg([r["gf"] for r in rows]),
         "avg_conceded": avg([r["ga"] for r in rows]),
         "btts": pct([r["gf"] > 0 and r["ga"] > 0 for r in rows]),
         "scored_in": pct([r["gf"] > 0 for r in rows]),
         "fh_o0.5": pct([x > 0.5 for x in fh]), "fh_o1.5": pct([x > 1.5 for x in fh])}
    s.update({f"o{l}": pct([t > l for t in tot]) for l in LINES})
    # Team-goal records: how often this side scored / conceded no more than n (team totals, markets 19/20).
    for n in (0, 1, 2):
        s[f"scored_le{n}"] = pct([r["gf"] <= n for r in rows])
        s[f"conceded_le{n}"] = pct([r["ga"] <= n for r in rows])
    return s


def h2h(home_uid, away_uid):
    ms = sorted(gismo(f"stats_team_versusrecent/{home_uid}/{away_uid}").get("matches") or [],
                key=lambda m: (m.get("time") or {}).get("uts") or 0, reverse=True)
    tot, btts, scores = [], [], []
    for m in ms:
        res = m.get("result") or {}
        if res.get("home") is None or res.get("away") is None:
            continue
        tot.append(res["home"] + res["away"]); btts.append(res["home"] > 0 and res["away"] > 0)
        scores.append(f"{res['home']}-{res['away']}")
        if len(tot) == H2H_N:
            break
    s = {"n": len(tot), "avg_goals": avg(tot), "btts": pct(btts), "scores": " ".join(scores)}
    s.update({f"o{l}": pct([t > l for t in tot]) for l in LINES})
    return s


def season(sid, home_uid, away_uid):
    d = gismo(f"stats_season_overunder/{sid}")
    lg = (d.get("league") or {}).get("totals") or {}
    ft = lg.get("ft") or {}
    out = {"matches": lg.get("matches"), "avg_goals": ((lg.get("goalsscored") or {}).get("ft") or {}).get("average")}
    out.update({f"o{l}": round(100 * ft[str(l)]["over"]) if str(l) in ft else None for l in LINES})
    for side, uid in (("home", home_uid), ("away", away_uid)):
        t = (d.get("stats") or {}).get(str(uid)) or {}
        line = ((t.get("total") or {}).get("ft") or {}).get("2.5") or {}
        n = (line.get("over") or 0) + (line.get("under") or 0)
        out[f"{side}_o2.5"] = round(100 * line["over"] / n) if n else None
        out[f"{side}_n"] = n or None
    return out


def one(g):
    row = dict(g)
    info = gismo(f"match_info/{g['event_id'].split(':')[-1]}")
    teams = (info.get("match") or {}).get("teams") or {}
    hu, au = (teams.get("home") or {}).get("uid"), (teams.get("away") or {}).get("uid")
    sid = (info.get("season") or {}).get("_id")
    if not (hu and au):
        row["stats"] = None
        return row
    hr, ar = team_rows(hu), team_rows(au)
    st = {"home": summarise(hr[:LAST_N]), "away": summarise(ar[:LAST_N]),
          "home_at_home": summarise([r for r in hr if r["venue"] == "H"][:VENUE_N]),
          "away_away": summarise([r for r in ar if r["venue"] == "A"][:VENUE_N]),
          "h2h": h2h(hu, au), "season": season(sid, hu, au) if sid else {}}
    # One blended number per line: the two teams' last-10 rates averaged.
    st["blend"] = {k: (round((st["home"][k] + st["away"][k]) / 2) if st["home"][k] is not None and st["away"][k] is not None else None)
                   for k in ("o0.5", "o1.5", "o2.5", "o3.5", "btts")}
    st["blend"]["exp_goals"] = round((st["home"]["avg_scored"] + st["away"]["avg_conceded"]
                                      + st["away"]["avg_scored"] + st["home"]["avg_conceded"]) / 2, 2) \
        if st["home"]["n"] and st["away"]["n"] else None
    for line, col in (("o1.5", "over1.5"), ("o2.5", "over2.5"), ("o3.5", "over3.5"), ("btts", "gg")):
        # In-play prices reflect the score so far; only a pre-match price compares with form.
        price, rate = (g.get(col) if g["status"] == "upcoming" else None), st["blend"][line]
        st["blend"][f"{line}_implied"] = round(100 / price) if price else None
        st["blend"][f"{line}_edge"] = rate - round(100 / price) if price and rate is not None else None
    row["stats"] = st
    return row


def main():
    global TOKEN
    day = sys.argv[1] if len(sys.argv) > 1 else str(dt.datetime.now(dt.timezone.utc).date())
    games = json.load(open(os.path.join(HERE, "out", f"games-{day}.json")))
    TOKEN = token(games)
    with ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(one, games))
    base = os.path.join(HERE, "out", f"goals-{day}")
    json.dump(rows, open(base + ".json", "w"), indent=1, ensure_ascii=False)
    write_csv(base + ".csv", rows)
    write_html(base + ".html", day, rows)
    ok = [r for r in rows if r["stats"]]
    print(f"{len(ok)}/{len(rows)} games have Sportradar stats ({len(gismo.cache_info()._asdict()) and gismo.cache_info().currsize} feeds fetched)")
    print("no stats:", ", ".join(f"{r['home_team']} v {r['away_team']}" for r in rows if not r["stats"]) or "-")
    print("wrote", base + ".{json,csv,html}")


def flat(r):
    out = {k: r[k] for k in ("event_id", "kickoff_utc", "status", "country", "league", "home_team", "away_team",
                             "over1.5", "over2.5", "under2.5", "over3.5", "gg", "ng")}
    for sect, d in (r["stats"] or {}).items():
        for k, v in d.items():
            out[f"{sect}.{k}"] = v
    return out


def write_csv(path, rows):
    flats = [flat(r) for r in rows]
    keys = list(dict.fromkeys(k for f in flats for k in f))
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); w.writerows(flats)


def write_html(path, day, rows):
    e = html.escape
    def cell(v, good=None, signed=False):
        if v is None: return "<td class=na>–</td>"
        cls = ""
        if good is not None: cls = "hi" if v >= good else "lo" if v <= 100 - good else ""
        if signed: cls = "hi" if v >= 10 else "lo" if v <= -10 else ""; v = f"{v:+d}"
        return f"<td class='{cls}'>{v}</td>"
    def pair(a, b, good=70):
        return cell(a, good) + cell(b, good)
    body = []
    for r in sorted(rows, key=lambda r: -((r["stats"] or {}).get("blend", {}).get("o2.5") or -1)):
        s = r["stats"]
        lead = (f"<td>{r['kickoff_utc']}<br><small>{e(r['status'])}</small></td><td><small>{e(r['country'])}</small><br>{e(r['league'])}</td>"
                f"<td>{e(r['home_team'])}<br>{e(r['away_team'])}</td>")
        if not s:
            body.append(f"<tr>{lead}<td colspan=22 class=na>No Sportradar stats for this game</td></tr>"); continue
        h, a, hh, aa, x, ss, b = s["home"], s["away"], s["home_at_home"], s["away_away"], s["h2h"], s["season"], s["blend"]
        body.append(f"<tr>{lead}"
            + cell(b["exp_goals"]) + cell(b["o1.5"], 70) + cell(b["o2.5"], 70) + cell(b["o3.5"], 70) + cell(b["btts"], 70)
            + pair(h["o2.5"], a["o2.5"]) + pair(hh["o2.5"], aa["o2.5"])
            + f"<td>{h['avg_scored']}–{h['avg_conceded']}<br>{a['avg_scored']}–{a['avg_conceded']}</td>"
            + pair(h["fh_o0.5"], a["fh_o0.5"])
            + f"<td title='{e(x['scores'])}'>{x['n']}</td>" + cell(x["avg_goals"]) + cell(x["o2.5"], 70) + cell(x["btts"], 70)
            + cell(ss.get("avg_goals")) + cell(ss.get("o2.5"), 60)
            + cell(r.get("over2.5")) + cell(b["o2.5_implied"]) + cell(b["o2.5_edge"], signed=True)
            + cell(r.get("gg")) + cell(b["btts_edge"], signed=True) + "</tr>")
    open(path, "w").write(f"""<!doctype html><meta charset=utf-8><title>Goal stats {day}</title>
<style>:root{{--hi:#d9f5df;--lo:#fde2e2}}body{{font:13px system-ui;margin:16px;background:#fff;color:#111}}
table{{border-collapse:collapse;width:100%}}td,th{{padding:4px 6px;border-bottom:1px solid #e3e3e3;text-align:right;vertical-align:top;white-space:nowrap}}
td:nth-child(-n+3),th:nth-child(-n+3){{text-align:left;white-space:normal}}thead th{{position:sticky;background:#f4f4f4;font-weight:600}}
thead tr:first-child th{{top:0}}thead tr:nth-child(2) th{{top:24px;font-weight:400;font-size:12px}}
.hi{{background:var(--hi)}}.lo{{background:var(--lo)}}.na{{color:#aaa}}small{{color:#777}}input{{padding:6px;width:300px;margin-bottom:8px}}
p{{color:#555;max-width:900px}}</style>
<h2>Goal stats — SportyBet football, {day} ({sum(1 for r in rows if r['stats'])} of {len(rows)} games with stats)</h2>
<p>Sorted by blended Over 2.5 rate. <b>Last 10</b> = each team's last 10 matches in any competition; <b>venue</b> = home side's last {VENUE_N} home games / away side's last {VENUE_N} away games;
<b>season</b> = this competition this season. <b>Exp goals</b> = (home scored + away conceded + away scored + home conceded) / 2 per game.
<b>Edge</b> (pre-match games only; live odds are in-play) = blended stat rate minus the probability implied by SportyBet's price (includes the bookmaker margin, so 0 is not break-even). Hover H2H count for the scores. Green ≥70%, red ≤30%.</p>
<input placeholder="Filter team / league…" oninput="for(const r of document.querySelectorAll('tbody tr'))r.hidden=!r.textContent.toLowerCase().includes(this.value.toLowerCase())">
<table><thead><tr><th rowspan=2>KO</th><th rowspan=2>League</th><th rowspan=2>Match</th><th colspan=5>Blend (last 10 both sides)</th>
<th colspan=2>O2.5 last 10</th><th colspan=2>O2.5 venue</th><th rowspan=2>GF–GA avg<br>H / A</th><th colspan=2>1H O0.5</th>
<th colspan=4>Head to head</th><th colspan=2>League season</th><th colspan=3>O2.5 market</th><th colspan=2>GG market</th></tr>
<tr><th>Exp goals</th><th>O1.5%</th><th>O2.5%</th><th>O3.5%</th><th>GG%</th><th>H</th><th>A</th><th>H@H</th><th>A@A</th><th>H</th><th>A</th>
<th>n</th><th>avg</th><th>O2.5%</th><th>GG%</th><th>avg</th><th>O2.5%</th><th>odds</th><th>impl%</th><th>edge</th><th>odds</th><th>edge</th></tr></thead>
<tbody>{''.join(body)}</tbody></table>""")


if __name__ == "__main__":
    main()
