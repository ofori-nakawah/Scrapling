"""Mint a Sportradar gismo token with Scrapling's browser: open a pre-match SportyBet
match page, click Stats, and read T=exp=... off the first widget request."""
import json, os, sys, time
from scrapling.fetchers import DynamicFetcher, Fetcher

GISMO = "https://widgets.fn.sportradar.com/common/en/Etc:UTC/gismo"
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".gismo-token.json")
CLICK_STATS = """() => { const el=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&/^stats$/i.test((e.textContent||'').trim())); if(el) el.click(); return !!el; }"""


def works(token, match_id):
    d = Fetcher.get(f"{GISMO}/match_info/{match_id.split(':')[-1]}?{token}",
                    headers={"user-agent": UA, "referer": "https://www.sportybet.com/"}).json()
    return d["doc"][0].get("event") != "exception"


def harvest(match_id):
    found = []
    def action(page):
        page.on("request", lambda r: found.append(r.url) if "widgets.fn.sportradar.com" in r.url and "T=exp=" in r.url else None)
        for _ in range(40):
            if found: break
            page.wait_for_timeout(500)
            try: page.evaluate(CLICK_STATS)
            except Exception: pass
    DynamicFetcher.fetch(f"https://www.sportybet.com/gh/m/sport/football/x/x/x_vs_x/{match_id}",
                         headless=True, page_action=action, useragent=UA, timeout=60000)
    return "T=" + found[0].split("T=")[1] if found else None


def mint(candidates):
    """candidates: pre-match sr:match ids, soonest first (needs >=30 min to kick-off)."""
    for mid in candidates[:4]:
        tok = harvest(mid)
        if tok and works(tok, mid):
            exp = int(tok.split("exp=")[1].split("~")[0])
            return {"token": tok, "exp": exp, "minted": int(time.time())}
        print(f"  no token from {mid}", file=sys.stderr)
    sys.exit("could not mint a gismo token")


if __name__ == "__main__":
    t = mint(sys.argv[1:])
    json.dump(t, open(TOKEN_FILE, "w"))
    print(f"token ok, valid {(t['exp']-time.time())/3600:.1f}h -> {TOKEN_FILE}")
