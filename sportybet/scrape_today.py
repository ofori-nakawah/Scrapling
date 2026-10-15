"""Scrape every SportyBet (Ghana) football game kicking off today, via Scrapling.

    docker run --rm -v "$PWD:/work" --entrypoint uv scrapling:local run python /work/scrape_today.py [YYYY-MM-DD]

Reads the public list feed (no login). Writes out/games-<date>.{json,csv,html}.
"""
import csv, datetime as dt, html, json, os, sys
from scrapling.fetchers import Fetcher

URL = "https://www.sportybet.com/api/gh/factsCenter/wapConfigurableEventsByOrder"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
STATUS = {0: "upcoming", 1: "live", 2: "suspended", 3: "ended", 4: "ended"}


def pages():
    page = 1
    while page <= 60:
        r = Fetcher.post(URL, json={"sportId": "sr:sport:1", "pageSize": 100, "pageNum": page,
                                    "marketId": "1,10,14,18,19,20,29"}, stealthy_headers=True, timeout=45)
        d = r.json()
        if d.get("bizCode") != 10000:
            sys.exit(f"API error {d.get('bizCode')}: {d.get('message')}")
        yield d["data"]
        if not d["data"].get("moreEvents"):
            return
        page += 1


def odds(ev):
    """{(market, specifier, outcome): price} from the open copy of each market."""
    best = {}
    for m in ev.get("markets", []):
        key = (m["id"], m.get("specifier") or "")
        if key in best and best[key].get("status") == 0:
            continue                      # already have the open copy
        best[key] = m
    out = {}
    for (mid, spec), m in best.items():
        if m.get("status") not in (0, None):
            continue                      # suspended / settled market
        for o in m.get("outcomes", []):
            try:
                v = float(o["odds"])
            except (KeyError, TypeError, ValueError):
                continue
            if o.get("isActive", 1) and v > 1.0:
                out[(mid, spec, o["id"])] = v
    return out


COLS = [("home", ("1", "", "1")), ("draw", ("1", "", "2")), ("away", ("1", "", "3")),
        ("1X", ("10", "", "9")), ("12", ("10", "", "10")), ("X2", ("10", "", "11")),
        ("over1.5", ("18", "total=1.5", "12")), ("under1.5", ("18", "total=1.5", "13")),
        ("over2.5", ("18", "total=2.5", "12")), ("under2.5", ("18", "total=2.5", "13")),
        ("over3.5", ("18", "total=3.5", "12")), ("under3.5", ("18", "total=3.5", "13")),
        ("gg", ("29", "", "74")), ("ng", ("29", "", "76"))]


def main():
    day = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.datetime.now(dt.timezone.utc).date()
    rows, seen, scanned = [], set(), 0
    for data in pages():
        for t in data.get("tournaments", []):
            for ev in t.get("events", []):
                if ev["eventId"] in seen:
                    continue              # the feed overlaps pages
                seen.add(ev["eventId"]); scanned += 1
                ko = dt.datetime.fromtimestamp(ev["estimateStartTime"] / 1000, dt.timezone.utc)
                if ko.date() != day:
                    continue
                cat = ev["sport"]["category"]
                px = odds(ev)
                row = {"event_id": ev["eventId"], "game_id": ev.get("gameId"),
                       "kickoff_utc": ko.strftime("%H:%M"), "country": cat["name"],
                       "league": cat["tournament"]["name"], "home_team": ev["homeTeamName"],
                       "away_team": ev["awayTeamName"], "status": STATUS.get(ev.get("status"), ev.get("status")),
                       "score": ev.get("setScore") or "", "clock": ev.get("playedSeconds") or ev.get("matchStatus") or "",
                       "markets": ev.get("totalMarketSize")}
                row.update({name: px.get(k) for name, k in COLS})
                rows.append(row)
    rows.sort(key=lambda r: (r["kickoff_utc"], r["country"], r["league"]))
    os.makedirs(OUT, exist_ok=True)
    base = os.path.join(OUT, f"games-{day}")
    json.dump(rows, open(base + ".json", "w"), indent=1, ensure_ascii=False)
    with open(base + ".csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["event_id"]); w.writeheader(); w.writerows(rows)
    write_html(base + ".html", day, rows)
    by = {}
    for r in rows: by[r["status"]] = by.get(r["status"], 0) + 1
    print(f"scanned {scanned} events; {len(rows)} kick off {day} UTC: {by}")
    print(f"leagues: {len({(r['country'], r['league']) for r in rows})}")
    print("wrote", base + ".{json,csv,html}")


def write_html(path, day, rows):
    e = html.escape
    f = lambda v: "" if v is None else f"{v:.2f}"
    head = "".join(f"<th>{e(n)}</th>" for n, _ in COLS)
    body = []
    for r in rows:
        live = f"<b>{e(r['score'])}</b> <small>{e(str(r['clock']))}</small>" if r["status"] == "live" else e(r["status"])
        body.append(f"<tr class='{e(r['status'])}'><td>{r['kickoff_utc']}</td><td><small>{e(r['country'])}</small><br>{e(r['league'])}</td>"
                    f"<td>{e(r['home_team'])}<br>{e(r['away_team'])}</td><td>{live}</td>"
                    + "".join(f"<td>{f(r[n])}</td>" for n, _ in COLS) + "</tr>")
    open(path, "w").write(f"""<!doctype html><meta charset=utf-8><title>SportyBet games {day}</title>
<style>body{{font:13px system-ui;margin:16px;background:#fff;color:#111}}table{{border-collapse:collapse;width:100%}}
td,th{{padding:4px 6px;border-bottom:1px solid #ddd;text-align:right;vertical-align:top}}td:nth-child(-n+4),th:nth-child(-n+4){{text-align:left}}
th{{position:sticky;top:0;background:#f4f4f4}}tr.live td{{background:#fff6e0}}tr.ended td{{color:#999}}
input{{padding:6px;width:300px;margin-bottom:8px}}small{{color:#777}}</style>
<h2>SportyBet Ghana — football, {day} ({len(rows)} games)</h2>
<input placeholder="Filter team / league…" oninput="for(const r of document.querySelectorAll('tbody tr'))r.hidden=!r.textContent.toLowerCase().includes(this.value.toLowerCase())">
<table><thead><tr><th>KO (UTC)</th><th>League</th><th>Match</th><th>Status</th>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>""")


if __name__ == "__main__":
    main()
