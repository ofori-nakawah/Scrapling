"""Create a SportyBet booking code from live legs (eventId/market/specifier/outcome). Nothing is staked."""
import json, sys
from scrapling.fetchers import Fetcher


def book(legs):
    """Booking code for live legs ["eventId/market/specifier/outcome", ...]; exits on failure."""
    sel = []
    for leg in legs:
        eid, mkt, spec, out = leg.split("/")
        s = {"eventId": eid, "marketId": mkt, "outcomeId": out, "productId": 1, "sportId": "sr:sport:1"}
        if spec: s["specifier"] = spec
        sel.append(s)
    r = Fetcher.post("https://www.sportybet.com/api/gh/orders/share", json={"selections": sel, "orderType": 2},
                     headers={"user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
                              "content-type": "application/json", "accept": "application/json"})
    if r.status != 200 or not r.body.lstrip().startswith(b"{"):
        sys.exit(f"booking failed: HTTP {r.status}: {r.body[:300]!r}")
    d = r.json()
    if d.get("bizCode") != 10000:
        sys.exit(f"booking failed: {d.get('bizCode')} {d.get('message')}")
    return d["data"]["shareCode"]


def status(code):
    """Each leg of a booking code: {event, status (0 pre, 1 live, 3/4 ended), winning (1, 0 or None)}."""
    r = Fetcher.get(f"https://www.sportybet.com/api/gh/orders/share/{code}",
                    headers={"user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
                             "accept": "application/json"})
    d = r.json()
    if d.get("bizCode") != 10000:
        sys.exit(f"status failed: {d.get('bizCode')} {d.get('message')}")
    out = []
    for ev in d["data"]["outcomes"]:
        o = ev["markets"][0]["outcomes"][0]
        try:
            odds = float(o.get("odds"))
        except (TypeError, ValueError):
            odds = None
        out.append({"event": ev["eventId"], "status": ev.get("status"), "score": ev.get("setScore"),
                    "winning": o.get("isWinning"), "odds": odds, "active": o.get("isActive", 1)})
    return out


if __name__ == "__main__":
    if sys.argv[1:2] == ["--status"]:
        print(json.dumps(status(sys.argv[2])))
    elif sys.argv[1:2] == ["--status-many"]:
        out = {}
        for code in sys.argv[2:]:
            try:
                out[code] = status(code)
            except SystemExit as e:
                out[code] = {"error": str(e)}
        print(json.dumps(out))
    else:
        print(book(sys.argv[1:]))
